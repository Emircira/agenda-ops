import asyncio
import os
import time
from loguru import logger
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from datetime import datetime

from celery import chain

from app.core.celery_app import celery_app
from app.db.session import AsyncSessionLocal
from app.models.core import Source, Content, SourceType
from app.providers.rss_provider import RSSProvider
from app.providers.youtube_provider import YouTubeProvider
from app.providers.x_provider import RapidXProvider, MockXProvider
from app.core.utils import pre_filter_content, extract_youtube_comments_as_articles


def run_async(coro):
    """Celery'nin senkron yapısı içinde Asyncio event loop çalıştırma zırhı."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def get_x_provider():
    """RapidAPI key yoksa Mock döner."""
    return RapidXProvider() if os.getenv("RAPIDAPI_KEY") else MockXProvider()


def _safe_parse_date(raw_date_str: str) -> datetime:
    """ISO tarih stringini datetime objesine çevirir. Hata olursa şimdiyi döner."""
    if not raw_date_str:
        return datetime.utcnow()
    try:
        return datetime.fromisoformat(raw_date_str.replace('Z', '+00:00')).replace(tzinfo=None)
    except Exception:
        return datetime.utcnow()


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
    }

    return {
        "source_id": source_id,
        "platform": "twitter",
        "external_id": post.get("external_id"),
        "author_name": author,
        "published_at": _safe_parse_date(post.get("published_at")),
        "text": post.get("text", ""),
        "content_type": "reply" if is_reply else "post",
        "url": f"https://twitter.com/x/status/{post.get('external_id')}",
        "domain": domain,
        "raw_json": safe_json,
        "is_analyzed": not pre_filter_content(post.get("text", ""))
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
            result = await db.execute(select(Source).where(Source.type == 'rss', Source.active == True))
            sources = result.scalars().all()

            all_source_infos = [
                {"name": "Google Haberler", "url": "https://news.google.com/rss?hl=tr&gl=TR&ceid=TR:tr", "id": None}
            ]
            for s in sources:
                all_source_infos.append({"name": s.name, "url": s.url, "id": s.id})

            total_added = 0
            for s_info in all_source_infos:
                try:
                    articles = await provider.fetch_feed(s_info["url"], s_info["name"])
                    for article in articles:
                        article['source_id'] = s_info["id"]
                        article['domain'] = 'general'
                        article['is_analyzed'] = not pre_filter_content(article.get('text', ''))

                    for i in range(0, len(articles), 100):
                        batch = articles[i:i+100]
                        if not batch:
                            continue
                        stmt = insert(Content).values(batch).on_conflict_do_nothing(index_elements=['external_id'])
                        res = await db.execute(stmt)
                        total_added += res.rowcount

                except Exception as e:
                    logger.error(f"RSS hata [{s_info['name']}]: {e}")

            await db.commit()
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
            result = await db.execute(select(Source).where(Source.type == 'youtube', Source.active == True))
            sources = result.scalars().all()

            total_added = 0
            for source in sources:
                try:
                    videos = await provider.fetch_channel_videos(source.url, max_results=100)
                    for video in videos:
                        vid_id = video["id"] if isinstance(video.get("id"), str) else video["id"]["videoId"]
                        comments = await provider.fetch_video_comments(vid_id, max_results=50)
                        articles = extract_youtube_comments_as_articles(
                            video, comments, source.id, getattr(source, 'domain', 'general')
                        )
                        for art in articles:
                            art['is_analyzed'] = not pre_filter_content(art.get('text', ''))

                        for i in range(0, len(articles), 100):
                            batch = articles[i:i+100]
                            if not batch:
                                continue
                            stmt = insert(Content).values(batch).on_conflict_do_nothing(index_elements=['external_id'])
                            res = await db.execute(stmt)
                            total_added += res.rowcount

                except Exception as e:
                    logger.error(f"YouTube hatası [{source.name}]: {e}")

            await db.commit()
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
            result = await db.execute(
                select(Source).where(
                    Source.type.in_(['twitter_self', 'twitter_competitor', 'twitter_trend', 'x']),
                    Source.active == True
                )
            )
            sources = result.scalars().all()

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
                source_name = source.name or source.url
                source_domain = getattr(source, 'domain', 'general') or 'general'

                logger.info(f"--- [{idx}/{len(sources)}] Kaynak: {source_name} (Tip: {source.type}) ---")

                try:
                    if source.type == 'twitter_trend':
                        posts = await provider.fetch_keyword_posts(source.url, limit=100)
                    else:
                        posts = await provider.fetch_mentions(source.url)

                    logger.info(f"  📦 Provider'dan {len(posts)} veri geldi.")

                    for post in posts:
                        try:
                            article = _post_to_article(post, source_id=source.id, domain=source_domain)
                            stmt = insert(Content).values(**article).on_conflict_do_nothing(
                                index_elements=['external_id']
                            )
                            res = await db.execute(stmt)
                            if res.rowcount > 0:
                                source_added += 1
                            else:
                                source_skipped += 1
                        except Exception as e:
                            logger.warning(f"  ⚠️ Kayıt hatası (atlandı): {e}")
                            source_skipped += 1

                    await db.commit()
                    grand_total_added += source_added
                    grand_total_skipped += source_skipped
                    source_results.append(f"✅ {source_name}: +{source_added} yeni, {source_skipped} mevcut")
                    logger.info(f"  ✅ {source_name}: {source_added} yeni, {source_skipped} mevcut.")

                except Exception as e:
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
    async def _task():
        logger.info("🐦 Celery: X/Twitter Gündem Araması Başladı")
        provider = get_x_provider()

        try:
            posts = await provider.fetch_keyword_posts("Türkiye Gündemi", limit=100)
        except Exception as e:
            logger.error(f"Gündem araması hatası: {e}")
            posts = []

        async with AsyncSessionLocal() as db:
            total_added = 0
            for post in posts:
                try:
                    article = _post_to_article(post, source_id=None, domain="general")
                    stmt = insert(Content).values(**article).on_conflict_do_nothing(index_elements=['external_id'])
                    res = await db.execute(stmt)
                    if res.rowcount > 0:
                        total_added += 1
                except Exception as e:
                    logger.warning(f"Gündem kayıt hatası: {e}")

            await db.commit()
            logger.info(f"✅ X/Twitter Gündem Tamamlandı. {total_added} yeni içerik.")
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
            posts = await provider.fetch_keyword_posts(target_person, limit=100)
        except Exception as e:
            logger.error(f"Kişi takibi hatası: {e}")
            posts = []

        async with AsyncSessionLocal() as db:
            total_added = 0
            for post in posts:
                try:
                    article = _post_to_article(post, source_id=None, domain="politics")
                    stmt = insert(Content).values(**article).on_conflict_do_nothing(index_elements=['external_id'])
                    res = await db.execute(stmt)
                    if res.rowcount > 0:
                        total_added += 1
                except Exception as e:
                    logger.warning(f"Kişi takibi kayıt hatası: {e}")

            await db.commit()
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
        from sqlalchemy import delete, not_

        cutoff_date = datetime.utcnow() - timedelta(days=days)
        async with AsyncSessionLocal() as db:
            stmt = delete(Content).where(
                Content.published_at < cutoff_date,
                not_(Content.external_id.ilike("deep_%"))
            )
            res = await db.execute(stmt)
            await db.commit()
            logger.info(f"✅ Temizlik Tamamlandı. {res.rowcount} eski içerik silindi.")

    return run_async(_task())


# =============================================================================
# CELERY CHAIN YARDIMCI — Fetch → AI Otomatik Tetikleme
# =============================================================================
def _trigger_analysis_chain(source_name: str):
    """
    Veri çekme görevi bittikten sonra AI analiz görevini otomatik tetikler.
    Fetch → Analyze akışını garantiler.
    """
    try:
        from app.workers.labeling_tasks import batch_analyze_contents
        logger.info(f"🔗 CHAIN: {source_name} Fetch tamamlandı → AI Analiz tetikleniyor...")
        batch_analyze_contents.apply_async(
            countdown=5,  # 5 saniye sonra başla (DB commit'in yerleşmesini bekle)
            queue="default",
        )
        logger.info(f"✅ CHAIN: {source_name} → batch_analyze_contents görevi kuyruğa eklendi.")
    except Exception as e:
        logger.error(f"❌ CHAIN tetikleme hatası ({source_name}): {e}")


# =============================================================================
# ORCHESTRATOR: Tüm veri çekme + analiz tek görevde
# =============================================================================
@celery_app.task(name="full_pipeline_orchestrator")
def full_pipeline_orchestrator():
    """
    Tam pipeline: RSS → YouTube → Twitter → AI Analiz.
    Manuel tetikleme veya test amaçlı kullanılır.
    """
    logger.info("🚀 FULL PIPELINE ORCHESTRATOR BAŞLADI")

    pipeline = chain(
        ingest_rss_all_sources.si(),
        ingest_youtube_all_sources.si(),
        ingest_x_all_sources.si(),
    )

    pipeline.apply_async()
    logger.info("✅ FULL PIPELINE kuyruğa eklendi (RSS → YouTube → Twitter → AI)")
    return "Pipeline started."