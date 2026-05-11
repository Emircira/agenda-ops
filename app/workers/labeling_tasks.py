import asyncio
import time
from loguru import logger
from sqlalchemy import select, and_

from app.core.celery_app import celery_app
from app.db.session import AsyncSessionLocal
from app.models.core import Content, ContentLabel
from app.services.gemini_service import GeminiAIClient
from app.core.utils import calculate_twitter_bot_likelihood


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
)
def batch_analyze_contents(self):
    """
    Production-Ready AI Analiz Görevi.
    • is_analyzed=False olan içerikleri 50'lik batch'ler halinde çeker
    • Her batch'i Gemini'ye gönderir (Gemini kendi içinde 20'lik mini-batch yapar)
    • Sonuçları ContentLabel tablosuna yazar + Content.is_analyzed=True günceller
    • Hata olan kayıtları atlar, diğerleri devam eder
    • Platform bazlı filtreleme (twitter, youtube_comment, rss, tümü)
    """
    async def _task():
        logger.info("=" * 60)
        logger.info("🧠 Celery: Gemini AI Toplu Analiz BAŞLADI")
        logger.info("=" * 60)

        try:
            ai_client = GeminiAIClient()
        except Exception as e:
            logger.error(f"❌ GeminiAIClient başlatılamadı: {e}")
            return f"Error: GeminiAIClient init failed: {e}"

        if not ai_client.model:
            logger.error("❌ Gemini modeli yüklenemedi, analiz atlanıyor.")
            return "Error: Gemini model not available."

        # DB boyutu = 50 (Gemini mini-batch=20, bu yüzden 50=2.5 API çağrısı)
        DB_BATCH_SIZE = 50
        total_analyzed = 0
        total_failed = 0
        total_skipped = 0
        max_iterations = 1  # Triage sonrası tek çalışmada en fazla 50 içerik analiz edilir.

        async with AsyncSessionLocal() as db:
            for iteration in range(max_iterations):
                # Analiz edilmemiş içerikleri çek (sıralı, en eski önce)
                stmt = (
                    select(Content)
                    .where(Content.is_analyzed == False)
                    .order_by(Content.published_at.asc())
                    .limit(DB_BATCH_SIZE)
                )
                res = await db.execute(stmt)
                contents = res.scalars().all()

                if not contents:
                    logger.info(f"📭 Analiz edilecek içerik kalmadı (iterasyon {iteration + 1}).")
                    break

                logger.info(
                    f"📦 İterasyon {iteration + 1}: {len(contents)} içerik analiz ediliyor... "
                    f"(toplam şu ana kadar: {total_analyzed} başarılı, {total_failed} hatalı)"
                )

                # Gemini'ye gönderilecek veri
                batch_data = []
                for c in contents:
                    batch_data.append({
                        "id": str(c.id),
                        "text": c.text or "",
                        "platform": c.platform or "unknown",
                    })

                # Gemini AI analiz (mini-batch'ler halinde çalışır)
                try:
                    analysis_results = ai_client.analyze_batch(batch_data)
                except Exception as e:
                    logger.error(f"❌ Gemini batch analiz hatası: {e}")
                    # Bu batch'i atla ama devam et
                    # İçerikleri is_analyzed=True yap ki sonsuz döngüye girmesin
                    for c in contents:
                        c.is_analyzed = True
                    await db.commit()
                    total_skipped += len(contents)
                    continue

                # Sonuçları ID'ye göre indexle (hızlı eşleşme)
                results_by_id = {}
                for item in analysis_results:
                    item_id = str(item.get("id", ""))
                    if item_id:
                        results_by_id[item_id] = item

                # Her içerik için sonuçları DB'ye yaz
                for content in contents:
                    content_id_str = str(content.id)
                    match = results_by_id.get(content_id_str)

                    if match:
                        try:
                            # ContentLabel oluştur veya güncelle (UPSERT benzeri)
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

                            # Merge: Mevcut label varsa güncelle, yoksa ekle
                            await db.merge(label)
                            content.is_analyzed = True
                            total_analyzed += 1

                        except Exception as e:
                            logger.warning(f"⚠️ Label kayıt hatası (content {content_id_str[:8]}): {e}")
                            content.is_analyzed = True  # Sonsuz döngü engeli
                            total_failed += 1
                    else:
                        # Gemini bu içerik için sonuç üretemedi
                        logger.debug(f"Gemini sonuç bulunamadı: {content_id_str[:8]}")
                        content.is_analyzed = True  # Tekrar denemeye gerek yok
                        total_skipped += 1

                # Her iterasyon sonunda commit
                try:
                    await db.commit()
                except Exception as e:
                    logger.error(f"❌ DB commit hatası: {e}")
                    await db.rollback()
                    total_failed += len(contents)

                # İterasyonlar arası kısa bekleme (Gemini rate limit koruması)
                if iteration < max_iterations - 1:
                    await asyncio.sleep(3)

        summary = (
            f"🏁 AI Analiz Tamamlandı: "
            f"{total_analyzed} başarılı, {total_failed} hatalı, {total_skipped} atlandı"
        )
        logger.info("=" * 60)
        logger.info(summary)
        logger.info("=" * 60)
        return summary

    return run_async(_task())


# =============================================================================
# PLATFORM-SPESİFİK ANALİZ GÖREVLERİ (Opsiyonel — chain'den tetiklenebilir)
# =============================================================================
@celery_app.task(
    name="analyze_twitter_contents",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=30,
    max_retries=2,
)
def analyze_twitter_contents(self, fetch_result=None):
    """
    Sadece Twitter platformu için AI analizi.
    Celery chain'den fetch_result parametresi ile tetiklenebilir.
    """
    async def _task():
        logger.info(f"🐦 Twitter AI Analizi Başladı (tetikleyen: {fetch_result or 'manuel'})")

        try:
            ai_client = GeminiAIClient()
        except Exception as e:
            logger.error(f"GeminiAIClient başlatılamadı: {e}")
            return f"Error: {e}"

        if not ai_client.model:
            return "Error: Gemini model not available."

        DB_BATCH_SIZE = 50
        total_analyzed = 0

        async with AsyncSessionLocal() as db:
            for _ in range(5):  # Max 5 iterasyon (250 içerik)
                stmt = (
                    select(Content)
                    .where(
                        and_(
                            Content.is_analyzed == False,
                            Content.platform == "twitter"
                        )
                    )
                    .order_by(Content.published_at.asc())
                    .limit(DB_BATCH_SIZE)
                )
                res = await db.execute(stmt)
                contents = res.scalars().all()

                if not contents:
                    break

                batch_data = [{"id": str(c.id), "text": c.text or ""} for c in contents]
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
                            await db.merge(label)
                            total_analyzed += 1
                        except Exception as e:
                            logger.warning(f"Label hatası: {e}")
                    content.is_analyzed = True

                await db.commit()
                await asyncio.sleep(3)

        result = f"Twitter AI Analiz: {total_analyzed} içerik analiz edildi."
        logger.info(f"✅ {result}")
        return result

    return run_async(_task())


@celery_app.task(
    name="analyze_youtube_contents",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=30,
    max_retries=2,
)
def analyze_youtube_contents(self, fetch_result=None):
    """
    Sadece YouTube platformu için AI analizi.
    Celery chain'den fetch_result parametresi ile tetiklenebilir.
    """
    async def _task():
        logger.info(f"🎥 YouTube AI Analizi Başladı (tetikleyen: {fetch_result or 'manuel'})")

        try:
            ai_client = GeminiAIClient()
        except Exception as e:
            logger.error(f"GeminiAIClient başlatılamadı: {e}")
            return f"Error: {e}"

        if not ai_client.model:
            return "Error: Gemini model not available."

        DB_BATCH_SIZE = 50
        total_analyzed = 0

        async with AsyncSessionLocal() as db:
            for _ in range(5):
                stmt = (
                    select(Content)
                    .where(
                        and_(
                            Content.is_analyzed == False,
                            Content.platform == "youtube_comment"
                        )
                    )
                    .order_by(Content.published_at.asc())
                    .limit(DB_BATCH_SIZE)
                )
                res = await db.execute(stmt)
                contents = res.scalars().all()

                if not contents:
                    break

                batch_data = [{"id": str(c.id), "text": c.text or ""} for c in contents]
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
                            await db.merge(label)
                            total_analyzed += 1
                        except Exception as e:
                            logger.warning(f"Label hatası: {e}")
                    content.is_analyzed = True

                await db.commit()
                await asyncio.sleep(3)

        result = f"YouTube AI Analiz: {total_analyzed} içerik analiz edildi."
        logger.info(f"✅ {result}")
        return result

    return run_async(_task())


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