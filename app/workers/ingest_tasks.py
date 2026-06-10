import asyncio
import os
from loguru import logger
from sqlalchemy import select
from datetime import datetime

from celery import chain

from app.core.celery_app import celery_app
from app.db.session import AsyncSessionLocal
from app.models.core import Content, ContentEmbedding, ContentType
from app.providers.rss_provider import RSSProvider
from app.providers.youtube_provider import YouTubeProvider, YouTubeQuotaExceeded
from app.providers.x_provider import RapidXProvider
from app.core.utils import (
    pre_filter_content,
    is_retweet_like,
    extract_youtube_comments_as_articles,
    extract_youtube_video_stub_article,
    calculate_twitter_bot_likelihood,
    twitter_bot_signal_summary,
    select_ai_triage_candidates,
)
from app.repositories.content_repository import ContentRepository
from app.repositories.target_repository import TargetRepository
from app.repositories.vector_repository import VectorRepository
from app.services.embedding_service import generate_embedding


def run_async(coro):
    """Celery'nin senkron yapısı içinde Asyncio event loop çalıştırma zırhı."""
    return asyncio.run(coro)


def get_x_provider():
    """X/Twitter provider'ı yalnızca gerçek RapidAPI kimliğiyle başlatır."""
    if not (os.getenv("RAPIDAPI_KEY") or os.getenv("RAPID_API_KEY")):
        raise RuntimeError("RAPIDAPI_KEY/RAPID_API_KEY zorunludur; X için mock veri üretilmez.")
    return RapidXProvider()


def _safe_parse_date(raw_date_str: str) -> datetime:
    """ISO tarih stringini datetime objesine çevirir. Hata olursa şimdiyi döner."""
    if not raw_date_str:
        return datetime.utcnow()
    try:
        return datetime.fromisoformat(raw_date_str.replace('Z', '+00:00')).replace(tzinfo=None)
    except Exception:
        return datetime.utcnow()


async def _try_save_content_embedding(db, external_id: str, text: str) -> None:
    """
    Yeni kaydedilen içerik için document embedding üretir ve vector tablosuna yazar.
    API/DB hatalarında loglar; ingest akışını düşürmez.
    Eğer satırda zaten embedding varsa API çağrısı yapmaz.
    """
    eid = (external_id or "").strip()
    if not eid:
        return
    body = (text or "").strip()
    if len(body) < 8:
        return
    try:
        cid_row = await db.execute(select(Content.id).where(Content.external_id == eid).limit(1))
        cid = cid_row.scalar_one_or_none()
        if cid is None:
            return
        exists = await db.execute(
            select(ContentEmbedding.id).where(ContentEmbedding.content_id == cid).limit(1)
        )
        if exists.scalar_one_or_none() is not None:
            return
        vec = generate_embedding(body, task_type="retrieval_document")
        vr = VectorRepository(db)
        await vr.save_embedding(cid, vec, commit=False)
    except Exception as exc:
        logger.warning(f"Vektör üretimi atlandı (external_id={eid!r}): {exc}")


def _post_to_article(post: dict, source_id=None, domain="general") -> dict:
    """
    Provider'dan gelen post dict'ini DB-ready article dict'ine çevirir.
    raw_json'a sadece serileştirilebilir (JSON-safe) veriyi koyar.
    author alanını her zaman string olarak garanti eder.
    """
    is_reply = post.get("target_type") == "twitter_reply"

    # author kesinlikle string olmalı
    author = post.get("author", "Unknown")
    if isinstance(author, dict):
        author = author.get("screen_name") or author.get("name") or "Unknown"
    author = str(author)

    # raw_json'da saklanacak temiz veri
    safe_json = {
        "external_id": post.get("external_id"),
        "text": post.get("text", "")[:500],
        "author": author,
        "target_type": post.get("target_type"),
        "target_name": post.get("target_name"),
        "metrics": {
            "likes": post.get("_likes", 0),
            "retweets": post.get("_retweets", 0),
            "replies": post.get("_replies", 0),
        },
        "account_metrics": post.get("_account_metrics") or {},
    }
    if is_reply:
        safe_json["parent_post_snippet"] = (post.get("_parent_post_snippet") or "")[:1600]
        safe_json["reply_plain"] = (post.get("_reply_plain_text") or "")[:900]
        safe_json["reaction_context"] = True
        safe_json["text"] = post.get("text", "")[:2400]

    safe_json["bot_likelihood"] = calculate_twitter_bot_likelihood(safe_json)
    safe_json["bot_signals"] = twitter_bot_signal_summary(safe_json)

    return {
        "source_id": source_id,
        "platform": "twitter",
        "external_id": post.get("external_id"),
        "author_name": author,
        "published_at": _safe_parse_date(post.get("published_at")),
        "text": (post.get("text", "") or "")[:8000],
        "content_type": ContentType.reply if is_reply else ContentType.post,
        "url": f"https://twitter.com/x/status/{post.get('external_id')}",
        "domain": domain,
        "raw_json": safe_json,
        "is_analyzed": False,
    }


# =============================================================================
# RSS INGESTION (+ AI Chain)
# =============================================================================
@celery_app.task(
    name="ingest_rss_all_sources",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=60,
    retry_backoff_max=600,
    max_retries=2,
    acks_late=True,
)
def ingest_rss_all_sources(self):
    async def _task():
        logger.info("📡 Celery: RSS Ingestion Başladı")
        provider = RSSProvider()

        async with AsyncSessionLocal() as db:
            target_repo = TargetRepository(db)
            content_repo = ContentRepository(db)
            sources = await target_repo.list_active_rss_maps()

            all_source_infos = [
                {"name": "Google Haberler", "url": "https://news.google.com/rss?hl=tr&gl=TR&ceid=TR:tr", "id": None}
            ]
            for s in sources:
                all_source_infos.append({"name": s["name"], "url": s["url"], "id": s["id"], "domain": s.get("domain") or "general"})

            total_added = 0
            for s_info in all_source_infos:
                try:
                    articles = await provider.fetch_feed(s_info["url"], s_info["name"])
                    for article in articles:
                        article['source_id'] = s_info["id"]
                        article['domain'] = s_info.get("domain", "general")
                        article['is_analyzed'] = True

                    for i in range(0, len(articles), 100):
                        batch = articles[i:i+100]
                        if not batch:
                            continue
                        n = await content_repo.insert_many_ignore_conflict(batch)
                        total_added += n
                        if n > 0:
                            for art in batch:
                                await _try_save_content_embedding(
                                    db,
                                    str(art.get("external_id") or ""),
                                    str(art.get("text") or ""),
                                )

                except Exception as e:
                    logger.error(f"RSS hata [{s_info['name']}]: {e}")

            await content_repo.commit()
            logger.info(f"✅ RSS Ingestion Tamamlandı. {total_added} yeni içerik eklendi.")
            return f"RSS: {total_added} yeni içerik"

    result = run_async(_task())

    # ──── CHAIN: RSS Fetch bitti → AI analizini tetikle ────
    _trigger_analysis_chain("RSS")

    return result


# =============================================================================
# YOUTUBE INGESTION (+ AI Chain)
# =============================================================================
@celery_app.task(
    name="ingest_youtube_all_sources",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=60,
    retry_backoff_max=600,
    max_retries=2,
    acks_late=True,
)
def ingest_youtube_all_sources(self):
    async def _task():
        logger.info("🎥 Celery: YouTube Ingestion Başladı")
        provider = YouTubeProvider()

        async with AsyncSessionLocal() as db:
            target_repo = TargetRepository(db)
            content_repo = ContentRepository(db)
            sources = await target_repo.list_active_youtube_maps()

            total_added = 0
            for source in sources:
                source_id = source["id"]
                source_name = source["name"]
                source_url = source["url"]
                source_domain = source.get("domain") or "general"
                try:
                    videos = await provider.fetch_channel_videos(source_url, max_results=100)
                    for video in videos:
                        vid_id = video["id"] if isinstance(video.get("id"), str) else video["id"]["videoId"]
                        comments = await provider.fetch_video_comments(vid_id, max_results=50)
                        articles = extract_youtube_comments_as_articles(
                            video, comments, source_id, source_domain
                        )
                        if not articles:
                            stub = extract_youtube_video_stub_article(video, source_id, source_domain)
                            articles = [stub] if stub else []
                        for art in articles:
                            art['is_analyzed'] = True

                        for i in range(0, len(articles), 100):
                            batch = articles[i:i+100]
                            if not batch:
                                continue
                            n = await content_repo.insert_many_ignore_conflict(batch)
                            total_added += n
                            if n > 0:
                                for art in batch:
                                    await _try_save_content_embedding(
                                        db,
                                        str(art.get("external_id") or ""),
                                        str(art.get("text") or ""),
                                    )

                except Exception as e:
                    if isinstance(e, YouTubeQuotaExceeded):
                        logger.error(f"YouTube kotası doldu [{source_name}], diğer kaynaklara geçiliyor: {e}")
                        continue
                    logger.error(f"YouTube hatası [{source_name}]: {e}")

            await content_repo.commit()
            logger.info(f"✅ YouTube Ingestion Tamamlandı. {total_added} yeni içerik eklendi.")
            return f"YouTube: {total_added} yeni içerik"

    result = run_async(_task())

    # ──── CHAIN: YouTube Fetch bitti → AI analizini tetikle ────
    _trigger_analysis_chain("YouTube")

    return result


# =============================================================================
# TWITTER/X — KAYNAK BAZLI TARAMA (+ AI Chain)
# =============================================================================
@celery_app.task(
    name="ingest_x_all_sources",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=30,
    retry_backoff_max=300,
    max_retries=2,
    acks_late=True,
)
def ingest_x_all_sources(self):
    """
    Kayıtlı tüm Twitter kaynaklarını SIRAYLA ve GÜVENLİ şekilde tarar.
    • Kaynaklar paralel DEĞİL, sırayla → Rate-limit koruması
    • Her kaynak arasında 5sn bekleme
    • try-except HER KAYNAK İÇİN AYRI → 1 kaynak çökerse diğerleri devam eder
    • Deduplication: external_id ile ON CONFLICT DO NOTHING
    • Fetch tamamlanınca AI analiz otomatik tetiklenir (chain)
    """
    async def _task():
        logger.info("=" * 60)
        logger.info("🎯 Celery: X/Twitter Kaynak Taraması BAŞLADI")
        logger.info("=" * 60)

        provider = get_x_provider()

        async with AsyncSessionLocal() as db:
            target_repo = TargetRepository(db)
            content_repo = ContentRepository(db)
            sources = await target_repo.list_active_x_maps()

            if not sources:
                logger.warning("⚠️ Hiç aktif Twitter kaynağı bulunamadı!")
                return "No Twitter sources found."

            logger.info(f"📋 {len(sources)} aktif Twitter kaynağı bulundu.")

            grand_total_added = 0
            grand_total_skipped = 0
            source_results = []

            for idx, source in enumerate(sources, 1):
                source_added = 0
                source_skipped = 0
                source_id = source["id"]
                source_type = source["type"]
                source_url = source["url"]
                source_name = source["name"] or source_url
                source_domain = source.get("domain") or "general"

                logger.info(f"--- [{idx}/{len(sources)}] Kaynak: {source_name} (Tip: {source_type}) ---")

                try:
                    if source_type == 'twitter_trend':
                        posts = await provider.fetch_top_tweets_shallow(source_url, limit=17)
                    elif source_type == 'twitter_agency':
                        posts = await provider.fetch_from_channel(source_url)
                    else:
                        posts = await provider.fetch_mentions(source_url)

                    logger.info(f"  📦 Provider'dan {len(posts)} veri geldi.")

                    for post in posts:
                        try:
                            article = _post_to_article(post, source_id=source_id, domain=source_domain)
                            rc = await content_repo.insert_one_ignore_conflict(article)
                            await content_repo.commit()
                            if rc > 0:
                                source_added += 1
                                await _try_save_content_embedding(
                                    db,
                                    str(article.get("external_id") or ""),
                                    str(article.get("text") or ""),
                                )
                            else:
                                source_skipped += 1
                        except Exception as e:
                            await content_repo.rollback()
                            logger.warning(
                                "  ⚠️ Tweet kayıt hatası (atlandı) "
                                f"external_id={post.get('external_id')} text={post.get('text', '')[:120]!r}: {e}"
                            )
                            source_skipped += 1

                    await content_repo.commit()
                    grand_total_added += source_added
                    grand_total_skipped += source_skipped
                    source_results.append(f"✅ {source_name}: +{source_added} yeni, {source_skipped} mevcut")
                    logger.info(f"  ✅ {source_name}: {source_added} yeni, {source_skipped} mevcut.")

                except Exception as e:
                    await content_repo.rollback()
                    source_results.append(f"❌ {source_name}: HATA — {str(e)[:80]}")
                    logger.error(f"  ❌ {source_name} HATA (atlanıyor): {e}")

                if idx < len(sources):
                    logger.info(f"  ⏳ 5sn bekleniyor...")
                    await asyncio.sleep(5)

            logger.info(f"{'=' * 60}")
            logger.info(f"🏁 TAMAMLANDI: {grand_total_added} yeni + {grand_total_skipped} mevcut")
            for line in source_results:
                logger.info(f"   {line}")
            logger.info(f"{'=' * 60}")

            return f"Twitter: {grand_total_added} yeni içerik"

    result = run_async(_task())

    # ──── CHAIN: Twitter Fetch bitti → AI analizini tetikle ────
    _trigger_analysis_chain("Twitter")

    return result


# =============================================================================
# TWITTER/X — GÜNDEM (+ AI Chain)
# =============================================================================
@celery_app.task(
    name="ingest_x_daily_trends",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=30,
    max_retries=2,
    acks_late=True,
)
def ingest_x_daily_trends(self):
    """
    Zamanlanmış X gündem görevi — API maliyet kalkanı:
    • trends.php ile trend başlıkları
    • Her trend için yalnızca tek sayfa Top/Latest arama (5–10 tweet)
    • Reply / thread derinliği YOK
    """

    TREND_SAMPLE_COUNT = 7
    TWEETS_PER_TREND = 6

    async def _task():
        logger.info("🐦 Celery: X Gündem (düşük maliyet — trend + üst tweet örnekleri)")
        try:
            provider = get_x_provider()
        except Exception as e:
            logger.error(f"X provider başlatılamadı: {e}")
            return "Gündem: provider yok"

        try:
            trend_rows = await provider.fetch_trends()
        except Exception as e:
            logger.error(f"Trend listesi alınamadı: {e}")
            trend_rows = []

        all_posts: list = []
        seen_names: list = []
        seen_ids: set = set()

        for row in trend_rows:
            name = (row.get("target_name") or "").strip()
            if not name or name in seen_names:
                continue
            seen_names.append(name)
            if len(seen_names) > TREND_SAMPLE_COUNT:
                break
            try:
                chunk = await provider.fetch_top_tweets_shallow(name, limit=TWEETS_PER_TREND)
                for p in chunk:
                    eid = p.get("external_id")
                    if eid and eid not in seen_ids:
                        seen_ids.add(eid)
                        all_posts.append(p)
            except Exception as e:
                logger.warning(f"Trend '{name}' için örnek tweet atlandı: {e}")
            await asyncio.sleep(provider.API_DELAY)

        async with AsyncSessionLocal() as db:
            content_repo = ContentRepository(db)
            total_added = 0
            for post in all_posts:
                try:
                    article = _post_to_article(post, source_id=None, domain="general")
                    rc = await content_repo.insert_one_ignore_conflict(article)
                    if rc > 0:
                        total_added += 1
                        await _try_save_content_embedding(
                            db,
                            str(article.get("external_id") or ""),
                            str(article.get("text") or ""),
                        )
                except Exception as e:
                    logger.warning(f"Gündem kayıt hatası: {e}")

            await content_repo.commit()
            logger.info(
                f"✅ X Gündem (hafif): {len(seen_names)} trend başlığı tarandı, "
                f"{total_added} yeni tweet (toplam {len(all_posts)} aday)."
            )
            return f"Gündem: {total_added} yeni içerik"

    result = run_async(_task())

    # ──── CHAIN: Gündem Fetch bitti → AI analizini tetikle ────
    _trigger_analysis_chain("Gündem")

    return result


# =============================================================================
# TWITTER/X — KİŞİ TAKİBİ (+ AI Chain)
# =============================================================================
@celery_app.task(
    name="ingest_x_person_mention_posts",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=30,
    max_retries=2,
    acks_late=True,
)
def ingest_x_person_mention_posts(self, target_person: str = "Siyasi Lider"):
    async def _task():
        logger.info(f"👤 Celery: Kişi Takibi ({target_person})")
        provider = get_x_provider()

        try:
            posts = await provider.fetch_keyword_posts(target_person, limit=70)
        except Exception as e:
            logger.error(f"Kişi takibi hatası: {e}")
            posts = []

        async with AsyncSessionLocal() as db:
            content_repo = ContentRepository(db)
            total_added = 0
            for post in posts:
                try:
                    article = _post_to_article(post, source_id=None, domain="politics")
                    rc = await content_repo.insert_one_ignore_conflict(article)
                    if rc > 0:
                        total_added += 1
                        await _try_save_content_embedding(
                            db,
                            str(article.get("external_id") or ""),
                            str(article.get("text") or ""),
                        )
                except Exception as e:
                    logger.warning(f"Kişi takibi kayıt hatası: {e}")

            await content_repo.commit()
            logger.info(f"✅ Kişi Takibi Tamamlandı. {total_added} içerik.")
            return f"Kişi Takibi: {total_added} yeni içerik"

    result = run_async(_task())
    _trigger_analysis_chain("Kişi Takibi")
    return result


# =============================================================================
# VERİTABANI TEMİZLİK
# =============================================================================
@celery_app.task(name="cleanup_old_content")
def cleanup_old_content(days: int = 30):
    async def _task():
        logger.info(f"🧹 Temizlik Botu (Son {days} günden eskiler)")
        from datetime import timedelta

        cutoff_date = datetime.utcnow() - timedelta(days=days)
        async with AsyncSessionLocal() as db:
            content_repo = ContentRepository(db)
            deleted = await content_repo.delete_published_before_with_external_guard(
                cutoff_date, "deep_%"
            )
            await content_repo.commit()
            logger.info(f"✅ Temizlik Tamamlandı. {deleted} eski içerik silindi.")

    return run_async(_task())


# =============================================================================
# CELERY PIPELINE — Fetch → Clean/Triage → OSINT/Bot → AI → Stream
# =============================================================================
@celery_app.task(
    name="clean_and_triage_recent_content",
    bind=True,
    soft_time_limit=300,
    time_limit=360,
)
def clean_and_triage_recent_content(self, source_name: str = "GENEL", limit: int = 50, window