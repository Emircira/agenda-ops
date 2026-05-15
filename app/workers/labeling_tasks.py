import asyncio
from typing import Any, Dict, List

from loguru import logger

from app.core.celery_app import celery_app
from app.db.session import AsyncSessionLocal
from app.models.core import Content, ContentLabel
from app.services.gemini_service import GeminiAIClient
from app.core.utils import calculate_twitter_bot_likelihood
from app.repositories.content_repository import ContentRepository


def run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# =============================================================================
# ANA ANALİZ GÖREVİ — Twitter + YouTube + Tüm Platformlar
# =============================================================================
@celery_app.task(
    name="batch_analyze_contents",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=30,
    retry_backoff_max=300,
    max_retries=2,
    acks_late=True,
    soft_time_limit=1200,
    time_limit=1500,
)
def batch_analyze_contents(self) -> List[Dict[str, Any]]:
    """
    Production-Ready AI Analiz Görevi.
    • is_analyzed=False olan içerikleri 50'lik batch'ler halinde çeker
    • Her batch'i Gemini'ye gönderir (Gemini kendi içinde 20'lik mini-batch yapar)
    • Sonuçları ContentLabel tablosuna yazar + Content.is_analyzed=True günceller
    • Hata olan kayıtları atlar, diğerleri devam eder
    • Platform bazlı filtreleme (twitter, youtube_comment, rss, tümü)

    Dönüş: broker uyumu için JSON-safe list[dict] (bir sonraki .si() zaten tüketmez).
    """
    try:
        async def _task() -> List[Dict[str, Any]]:
            logger.info("=" * 60)
            logger.info("🧠 Celery: Gemini AI Toplu Analiz BAŞLADI")
            logger.info("=" * 60)

            try:
                ai_client = GeminiAIClient()
            except Exception as e:
                logger.error(f"❌ GeminiAIClient başlatılamadı: {e}")
                return [{"stage": "labeling", "ok": False, "error": f"GeminiAIClient init failed: {e}"}]

            if not ai_client.model:
                logger.error("❌ Gemini modeli yüklenemedi, analiz atlanıyor.")
                return [{"stage": "labeling", "ok": False, "error": "Gemini model not available."}]

            DB_BATCH_SIZE = 20
            total_analyzed = 0
            total_failed = 0
            total_skipped = 0
            max_iterations = 10

            async with AsyncSessionLocal() as db:
                content_repo = ContentRepository(db)
                for iteration in range(max_iterations):
                    rows = await content_repo.fetch_unanalyzed_with_source_category(DB_BATCH_SIZE)

                    if not rows:
                        logger.info(f"📭 Analiz edilecek içerik kalmadı (iterasyon {iteration + 1}).")
                        break

                    contents = [r[0] for r in rows]
                    src_cats = [r[1] for r in rows]

                    logger.info(
                        f"📦 İterasyon {iteration + 1}: {len(contents)} içerik analiz ediliyor... "
                        f"(toplam şu ana kadar: {total_analyzed} başarılı, {total_failed} hatalı)"
                    )

                    batch_data = []
                    for c, src_cat in zip(contents, src_cats):
                        batch_data.append({
                            "id": str(c.id),
                            "text": c.text or "",
                            "platform": c.platform or "unknown",
                            "source_category": src_cat or "general_agenda",
                        })

                    try:
                        analysis_results = ai_client.analyze_batch(batch_data)
                    except Exception as e:
                        logger.error(f"❌ Gemini batch analiz hatası: {e}")
                        for c in contents:
                            c.is_analyzed = True
                        await content_repo.commit()
                        total_skipped += len(contents)
                        continue

                    results_by_id = {}
                    for item in analysis_results:
                        item_id = str(item.get("id", ""))
                        if item_id:
                            results_by_id[item_id] = item

                    for content in contents:
                        content_id_str = str(content.id)
                        match = results_by_id.get(content_id_str)

                        if match:
                            try:
                                label = ContentLabel(
                                    content_id=content.id,
                                    topic=match.get("topic", "Genel"),
                                    frame=match.get("frame", "Genel"),
                                    stance=match.get("stance", "neutral"),
                                    target=match.get("target", "Bilinmiyor"),
                                    risk_level=match.get("risk_level", "low"),
                                    confidence=_safe_float(match.get("confidence"), 0.5),
                                    summary=match.get("summary", ""),
                                    sentiment_score=_safe_float(match.get("sentiment_score"), 0.0),
                                    manipulation_prob=_safe_float(match.get("manipulation_prob"), 0.0),
                                    bot_likelihood=_combined_bot_likelihood(match, content),
                                    sarcasm_detected=bool(match.get("sarcasm_detected", False)),
                                    crisis_score=_safe_int(match.get("crisis_score"), 0),
                                    sentiment=match.get("sentiment", "Nötr"),
                                )

                                await content_repo.merge_label(label)
                                content.is_analyzed = True
                                total_analyzed += 1

                            except Exception as e:
                                logger.warning(f"⚠️ Label kayıt hatası (content {content_id_str[:8]}): {e}")
                                content.is_analyzed = True
                                total_failed += 1
                        else:
                            logger.debug(f"Gemini sonuç bulunamadı: {content_id_str[:8]}")
                            content.is_analyzed = True
                            total_skipped += 1

                    try:
                        await content_repo.commit()
                    except Exception as e:
                        logger.error(f"❌ DB commit hatası: {e}")
                        await content_repo.rollback()
                        total_failed += len(contents)

                    if iteration < max_iterations - 1:
                        await asyncio.sleep(3)

            summary = (
                f"🏁 AI Analiz Tamamlandı: "
                f"{total_analyzed} başarılı, {total_failed} hatalı, {total_skipped} atlandı"
            )
            logger.info("=" * 60)
            logger.info(summary)
            logger.info("=" * 60)
            return [
                {
                    "stage": "labeling",
                    "summary": summary,
                    "total_analyzed": total_analyzed,
                    "total_failed": total_failed,
                    "total_skipped": total_skipped,
                }
            ]

        out = run_async(_task())
        return out if isinstance(out, list) else [{"stage": "labeling", "payload": str(out)}]
    except Exception as e:
        logger.exception(f"TASK FAILED: {e}")
        raise


# =============================================================================
# PLATFORM-SPESİFİK ANALİZ GÖREVLERİ (Opsiyonel — chain'den tetiklenebilir)
# =============================================================================
@celery_app.task(
    name="analyze_twitter_contents",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=30,
    max_retries=2,
    soft_time_limit=900,
    time_limit=1080,
)
def analyze_twitter_contents(self, fetch_result=None):
    """
    Sadece Twitter platformu için AI analizi.
    Celery chain'den fetch_result parametresi ile tetiklenebilir.
    """
    try:
        async def _task():
            logger.info(f"🐦 Twitter AI Analizi Başladı (tetikleyen: {fetch_result or 'manuel'})")

            try:
                ai_client = GeminiAIClient()
            except Exception as e:
                logger.error(f"GeminiAIClient başlatılamadı: {e}")
                return f"Error: {e}"

            if not ai_client.model:
                return "Error: Gemini model not available."

            DB_BATCH_SIZE = 20
            total_analyzed = 0

            async with AsyncSessionLocal() as db:
                content_repo = ContentRepository(db)
                for _ in range(5):
                    rows = await content_repo.fetch_unanalyzed_twitter_with_source_category(
                        DB_BATCH_SIZE
                    )

                    if not rows:
                        break

                    contents = [r[0] for r in rows]
                    src_cats = [r[1] for r in rows]

                    batch_data = [
                        {
                            "id": str(c.id),
                            "text": c.text or "",
                            "platform": c.platform or "unknown",
                            "source_category": src_cat or "general_agenda",
                        }
                        for c, src_cat in zip(contents, src_cats)
                    ]
                    analysis_results = ai_client.analyze_batch(batch_data)

                    results_by_id = {str(item.get("id", "")): item for item in analysis_results}

                    for content in contents:
                        match = results_by_id.get(str(content.id))
                        if match:
                            try:
                                label = ContentLabel(
                                    content_id=content.id,
                                    topic=match.get("topic", "Genel"),
                                    frame=match.get("frame", "Genel"),
                                    stance=match.get("stance", "neutral"),
                                    target=match.get("target", "Bilinmiyor"),
                                    risk_level=match.get("risk_level", "low"),
                                    confidence=_safe_float(match.get("confidence"), 0.5),
                                    summary=match.get("summary", ""),
                                    sentiment_score=_safe_float(match.get("sentiment_score"), 0.0),
                                    manipulation_prob=_safe_float(match.get("manipulation_prob"), 0.0),
                                    bot_likelihood=_combined_bot_likelihood(match, content),
                                    sarcasm_detected=bool(match.get("sarcasm_detected", False)),
                                    crisis_score=_safe_int(match.get("crisis_score"), 0),
                                    sentiment=match.get("sentiment", "Nötr"),
                                )
                                await content_repo.merge_label(label)
                                total_analyzed += 1
                            except Exception as e:
                                logger.warning(f"Label hatası: {e}")
                        content.is_analyzed = True

                    await content_repo.commit()
                    await asyncio.sleep(3)

            result = f"Twitter AI Analiz: {total_analyzed} içerik analiz edildi."
            logger.info(f"✅ {result}")
            return result

        return run_async(_task())
    except Exception as e:
        logger.exception(f"TASK FAILED: {e}")
        raise


@celery_app.task(
    name="analyze_youtube_contents",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=30,
    max_retries=2,
    soft_time_limit=900,
    time_limit=1080,
)
def analyze_youtube_contents(self, fetch_result=None):
    """
    Sadece YouTube platformu için AI analizi.
    Celery chain'den fetch_result parametresi ile tetiklenebilir.
    """
    try:
        async def _task():
            logger.info(f"🎥 YouTube AI Analizi Başladı (tetikleyen: {fetch_result or 'manuel'})")

            try:
                ai_client = GeminiAIClient()
            except Exception as e:
                logger.error(f"GeminiAIClient başlatılamadı: {e}")
                return f"Error: {e}"

            if not ai_client.model:
                return "Error: Gemini model not available."

            DB_BATCH_SIZE = 20
            total_analyzed = 0

            async with AsyncSessionLocal() as db:
                content_repo = ContentRepository(db)
                for _ in range(5):
                    rows = await content_repo.fetch_unanalyzed_youtube_comment_with_source_category(
                        DB_BATCH_SIZE
                    )

                    if not rows:
                        break

                    contents = [r[0] for r in rows]
                    src_cats = [r[1] for r in rows]

                    batch_data = [
                        {
                            "id": str(c.id),
                            "text": c.text or "",
                            "platform": c.platform or "unknown",
                            "source_category": src_cat or "general_agenda",
                        }
                        for c, src_cat in zip(contents, src_cats)
                    ]
                    analysis_results = ai_client.analyze_batch(batch_data)

                    results_by_id = {str(item.get("id", "")): item for item in analysis_results}

                    for content in contents:
                        match = results_by_id.get(str(content.id))
                        if match:
                            try:
                                label = ContentLabel(
                                    content_id=content.id,
                                    topic=match.get("topic", "Genel"),
                                    frame=match.get("frame", "Genel"),
                                    stance=match.get("stance", "neutral"),
                                    target=match.get("target", "Bilinmiyor"),
                                    risk_level=match.get("risk_level", "low"),
                                    confidence=_safe_float(match.get("confidence"), 0.5),
                                    summary=match.get("summary", ""),
                                    sentiment_score=_safe_float(match.get("sentiment_score"), 0.0),
                                    manipulation_prob=_safe_float(match.get("manipulation_prob"), 0.0),
                                    bot_likelihood=_combined_bot_likelihood(match, content),
                                    sarcasm_detected=bool(match.get("sarcasm_detected", False)),
                                    crisis_score=_safe_int(match.get("crisis_score"), 0),
                                    sentiment=match.get("sentiment", "Nötr"),
                                )
                                await content_repo.merge_label(label)
                                total_analyzed += 1
                            except Exception as e:
                                logger.warning(f"Label hatası: {e}")
                        content.is_analyzed = True

                    await content_repo.commit()
                    await asyncio.sleep(3)

            result = f"YouTube AI Analiz: {total_analyzed} içerik analiz edildi."
            logger.info(f"✅ {result}")
            return result

        return run_async(_task())
    except Exception as e:
        logger.exception(f"TASK FAILED: {e}")
        raise


# =============================================================================
# YARDIMCI FONKSİYONLAR
# =============================================================================
def _safe_float(value, default: float = 0.0) -> float:
    """Güvenli float dönüştürücü."""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _safe_int(value, default: int = 0) -> int:
    """Güvenli int dönüştürücü."""
    if value is None:
        return default
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return default


def _combined_bot_likelihood(match: dict, content: Content) -> float:
    """LLM skorunu Twitter metadata heuristiği ile güçlendirir."""
    llm_score = _safe_float(match.get("bot_likelihood"), 0.0)
    metadata_score = calculate_twitter_bot_likelihood(content.raw_json)
    return max(0.0, min(max(llm_score, metadata_score), 1.0))
