import asyncio
import logging
from app.db.base import engine, Base
# Tüm modelleri import et ki SQLAlchemy onları tanısın
from app.models.core import Source, Content, ContentLabel, ContentMetric, Opportunity

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def init_models():
    logger.info("Tablolar oluşturuluyor...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("✅ BAŞARILI! Tablolar veritabanına kuruldu.")
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(init_models())