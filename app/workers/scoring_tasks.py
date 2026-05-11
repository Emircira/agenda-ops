import asyncio
from datetime import datetime, timedelta
from loguru import logger
from sqlalchemy import select
from collections import defaultdict
from sqlalchemy import String
from app.core.celery_app import celery_app
from app.db.session import AsyncSessionLocal
from app.models.core import ContentLabel, Opportunity, Content
from app.services.scoring_service import ScoringService

def run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

@celery_app.task(name="build_opportunities")
def build_opportunities():
    async def _task():
        logger.info("🎯 Celery: Opportunity Score (Fırsat Kartları) Üretimi Başladı")
        service = ScoringService()
        
        async with AsyncSessionLocal() as db:
            try:
                # Son 24 saatteki etiketlenmiş içerikleri al
                time_threshold = datetime.utcnow() - timedelta(hours=24)
                await service.generate_opportunities(db, window_hours=24)
                logger.info("✅ Fırsat Kartları başarıyla üretildi.")
                return "Opportunities Generated Successfully."
            except Exception as e:
                logger.error(f"Fırsat kartı üretim hatası: {e}")
                return f"Error: {str(e)}"



    return run_async(_task())