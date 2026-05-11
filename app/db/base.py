from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool  # <--- YENİ EKLENEN KISIM

from app.core.config import settings
from app.models.base_class import Base
from app.models.core import Base, Source, Content, ContentMetric, ContentLabel, Opportunity


# Engine oluştururken "poolclass=NullPool" ekliyoruz.
# Bu, Celery gibi çoklu işlem (multiprocessing) ortamlarında
# "Future attached to a different loop" hatasını kesin olarak çözer.
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    poolclass=NullPool 
)

async_session = sessionmaker(
    engine, 
    class_=AsyncSession, 
    expire_on_commit=False
)