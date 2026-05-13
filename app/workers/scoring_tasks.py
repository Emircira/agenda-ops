import asyncio
from typing import Any, Dict, List

from loguru import logger
from app.core.celery_app import celery_app
from app.db.session import AsyncSessionLocal
from app.services.scoring_service import ScoringService


def run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(
    name="build_opportunities",
    bind=True,
    soft_time_limit=600,
    time_limit=720,
)
def build_opportunities(self) -> List[Dict[str, Any]]:
    try:
        async def _task() -> List[Dict[str, Any]]:
            logger.info("🎯 Celery: Opportunity Score (Fırsat Kartları) Üretimi Başladı")
            service = ScoringService()

            async with AsyncSessionLocal() as db:
                try:
                    await service.generate_opportunities(db, window_hours=24)
                    logger.info("✅ Fırsat Kartları başarıyla üretildi.")
                    return [{"stage": "scoring", "ok": True, "message": "Opportunities Generated Successfully."}]
                except Exception as e:
                    logger.exception(f"TASK FAILED: {e}")
                    return [{"stage": "scoring", "ok": False, "error": str(e)}]

        out = run_async(_task())
        return out if isinstance(out, list) else [{"stage": "scoring", "payload": str(out)}]
    except Exception as e:
        logger.exception(f"TASK FAILED: {e}")
        raise
