import asyncio
import logging
from sqlalchemy import text

from app.db.base import engine, Base
# Tüm modelleri import et ki SQLAlchemy onları tanısın
from app.models.core import Source, Content, ContentLabel, ContentMetric, Opportunity, ContentEmbedding

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def ensure_pgvector_extension(conn) -> None:
    await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

async def init_models():
    logger.info("pgvector eklentisi ve tablolar oluşturuluyor...")
    async with engine.begin() as conn:
        await ensure_pgvector_extension(conn)
        await conn.run_sync(Base.metadata.create_all)
    logger.info("✅ BAŞARILI! Tablolar veritabanına kuruldu.")
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(init_models())