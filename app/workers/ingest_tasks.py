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
from app.models.core import Source, Content, SourceType, ContentType
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
            result = await db.execute(
                select(Source.id, Source.name, Source.url, Source.domain)
                .where(Source.type == 'rss', Source.active == True)
            )
            sources = [dict(row) for row in result.mappings().all()]

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
            result = await db.execute(
                select(Source.id, Source.name, Source.url, Source.domain)
                .where(Source.type == 'youtube', Source.active == True)
            )
            sources = [dict(row) for row in result.mappings().all()]

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
                            stmt = insert(Content).values(batch).on_conflict_do_nothing(index_elements=['external_id'])
                            res = await db.execute(stmt)
                            total_added += res.rowcount

                except Exception as e:
                    if isinstance(e, YouTubeQuotaExceeded):
                        logger.error(f"YouTube kotası doldu [{source_name}], diğer kaynaklara geçiliyor: {e}")
                        continue
                    logger.error(f"YouTube hatası [{source_name}]: {e}")

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
                select(Source.id, Source.type, Source.name, Source.url, Source.domain).where(
                    Source.type.in_(
                        ['twitter_self', 'twitter_competitor', 'twitter_trend', 'twitter_agency', 'x']
                    ),
                    Source.active == True
                )
            )
            sources = [dict(row) for row in result.mappings().all()]

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
                            stmt = insert(Content).values(**article).on_conflict_do_nothing(
                                index_elements=['external_id']
                            )
                            res = await db.execute(stmt)
                            await db.commit()
                            if res.rowcount > 0:
                                source_added += 1
                            else:
                                source_skipped += 1
                        except Exception as e:
                            await db.rollback()
                            logger.warning(
                                "  ⚠️ Tweet kayıt hatası (atlandı) "
                                f"external_id={post.get('external_id')} text={post.get('text', '')[:120]!r}: {e}"
                            )
                            source_skipped += 1

                    await db.commit()
                    grand_total_added += source_added
                    grand_total_skipped += source_skipped
                    source_results.append(f"✅ {source_name}: +{source_added} yeni, {source_skipped} mevcut")
                    logger.info(f"  ✅ {source_name}: {source_added} yeni, {source_skipped} mevcut.")

                except Exception as e:
                    await db.rollback()
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
            total_added = 0
            for post in all_posts:
                try:
                    article = _post_to_article(post, source_id=None, domain="general")
                    stmt = insert(Content).values(**article).on_conflict_do_nothing(index_elements=['external_id'])
                    res = await db.execute(stmt)
                    if res.rowcount > 0:
                        total_added += 1
                except Exception as e:
                    logger.warning(f"Gündem kayıt hatası: {e}")

            await db.commit()
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
# CELERY PIPELINE — Fetch → Clean/Triage → OSINT/Bot → AI → Stream
# =============================================================================
@celery_app.task(
    name="clean_and_triage_recent_content",
    bind=True,
    soft_time_limit=300,
    time_limit=360,
)
def clean_and_triage_recent_content(self, source_name: str = "GENEL", limit: int = 50, window_hours: int = 6):
    """Etiketsiz içeriklerden AI adayı seçer; Celery broker uyumu için JSON-safe list[dict] döner."""
    try:
        async def _task():
            from datetime import timedelta
            from app.models.core import ContentLabel

            eff_hours = 24 if source_name == "Manual Trigger" else window_hours
            cutoff = datetime.utcnow() - timedelta(hours=eff_hours)
            async with AsyncSessionLocal() as db:
                stmt = (
                    select(Content)
                    .outerjoin(ContentLabel, Content.id == ContentLabel.content_id)
                    .where(
                        ContentLabel.content_id.is_(None),
                        Content.fetched_at >= cutoff,
                    )
                )
                res = await db.execute(stmt)
                contents = res.scalars().all()

                for content in contents:
                    content.is_analyzed = True

                if source_name == "Manual Trigger":
                    relaxed = []
                    for c in contents:
                        txt = c.text or ""
                        if pre_filter_content(txt) and not is_retweet_like(txt, c.raw_json):
                            relaxed.append(c)
                    relaxed.sort(key=lambda x: x.fetched_at or datetime.utcnow(), reverse=True)
                    selected = relaxed[:limit]
                else:
                    selected = select_ai_triage_candidates(contents, limit=limit)

                selected_ids = {content.id for content in selected}
                for content in contents:
                    if content.id in selected_ids:
                        content.is_analyzed = False

                await db.commit()
                logger.info(
                    f"🧹 TRIAGE [{source_name}]: {len(contents)} kayıt tarandı, "
                    f"{len(selected)} kayıt AI analizine seçildi (pencere={eff_hours}h)."
                )
                return [
                    {
                        "stage": "triage",
                        "source": source_name,
                        "total": len(contents),
                        "selected": len(selected),
                        "window_hours": eff_hours,
                    }
                ]

        rows = run_async(_task())
        return rows if isinstance(rows, list) else [{"stage": "triage", "payload": rows}]
    except Exception as e:
        logger.exception(f"TASK FAILED: {e}")
        raise


@celery_app.task(name="run_osint_bot_stage", bind=True, soft_time_limit=120, time_limit=150)
def run_osint_bot_stage(self, triage_payload=None):
    """Önceki aşamanın list[dict] çıktısını aynen iletir (zincir serileştirmesi için)."""
    try:
        logger.info(f"🛡️ OSINT/Bot aşaması tamamlandı: {triage_payload}")
        if triage_payload is None:
            return []
        if isinstance(triage_payload, list):
            return triage_payload
        if isinstance(triage_payload, dict):
            return [triage_payload]
        return [{"stage": "osint", "note": "unexpected_payload", "raw_type": type(triage_payload).__name__}]
    except Exception as e:
        logger.exception(f"TASK FAILED: {e}")
        raise


@celery_app.task(name="synthesize_ai_opportunities", bind=True, soft_time_limit=600, time_limit=720)
def synthesize_ai_opportunities(self):
    try:
        from app.workers.scoring_tasks import build_opportunities

        logger.info("🎯 AI sentez aşaması: fırsat kartları üretiliyor.")
        raw_result = build_opportunities()
        if isinstance(raw_result, list):
            return raw_result
        return [{"stage": "synthesize", "result": raw_result}]
    except Exception as e:
        logger.exception(f"TASK FAILED: {e}")
        raise


@celery_app.task(name="publish_stream_update", bind=True, soft_time_limit=60, time_limit=90)
def publish_stream_update(self, source_name: str = "GENEL"):
    try:
        logger.info(f"📡 Stream update hazır: {source_name} verileri /api/stream/recent endpointinden alınabilir.")
        return [{"stage": "publish", "source": source_name, "stream": "/api/stream/recent"}]
    except Exception as e:
        logger.exception(f"TASK FAILED: {e}")
        raise


def _trigger_analysis_chain(source_name: str):
    """
    Veri çekme görevi bittikten sonra kontrollü pipeline'ı sırayla çalıştırır.
    Fetch → Clean/Triage → OSINT/Bot → AI → Stream akışını garantiler.
    """
    try:
        from app.workers.labeling_tasks import batch_analyze_contents
        logger.info(f"🔗 CHAIN: {source_name} Fetch tamamlandı → temizleme/AI pipeline başlıyor...")
        pipeline = chain(
            clean_and_triage_recent_content.si(source_name),
            run_osint_bot_stage.s(),
            batch_analyze_contents.si(),
            synthesize_ai_opportunities.si(),
            publish_stream_update.si(source_name),
        )
        # Worker docker-compose'ta -Q celery ile çalışıyor; "default" kuyruğu dinlenmiyor.
        pipeline.apply_async(countdown=5)
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