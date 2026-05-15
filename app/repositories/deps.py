"""FastAPI Depends fabrikaları — tek istek içinde paylaşılan AsyncSession."""

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.repositories.complaint_cache_repository import ComplaintCacheRepository
from app.repositories.content_repository import ContentRepository
from app.repositories.opportunity_repository import OpportunityRepository
from app.repositories.target_repository import TargetRepository
from app.repositories.vector_repository import VectorRepository


def get_content_repository(db: AsyncSession = Depends(get_db)) -> ContentRepository:
    return ContentRepository(db)


def get_target_repository(db: AsyncSession = Depends(get_db)) -> TargetRepository:
    return TargetRepository(db)


def get_opportunity_repository(db: AsyncSession = Depends(get_db)) -> OpportunityRepository:
    return OpportunityRepository(db)


def get_complaint_cache_repository(db: AsyncSession = Depends(get_db)) -> ComplaintCacheRepository:
    return ComplaintCacheRepository(db)


def get_vector_repository(db: AsyncSession = Depends(get_db)) -> VectorRepository:
    return VectorRepository(db)
