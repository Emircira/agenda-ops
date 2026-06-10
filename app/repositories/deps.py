"""FastAPI Depends fabrikalari — tek istek icinde paylasilan AsyncSession."""

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.repositories.complaint_cache_repository import ComplaintCacheRepository
from app.repositories.content_repository import ContentRepository
from app.repositories.opportunity_repository import OpportunityRepository
from app.repositories.target_repository import TargetRepository
from app.repositories.vector_repository import VectorRepository
from app.repositories.macro_repository import MacroRepository
from app.repositories.source_repository import SourceRepository
from app.repositories.alert_repository import AlertRepository
from app.repositories.election_repository import ElectionRepository
from app.repositories.nisanyan_repository import NisanyanRepository
from app.repositories.radar_repository import RadarRepository


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


def get_macro_repository(db: AsyncSession = Depends(get_db)) -> MacroRepository:
    return MacroRepository(db)


def get_source_repository(db: AsyncSession = Depends(get_db)) -> SourceRepository:
    return SourceRepository(db)


def get_alert_repository(db: AsyncSession = Depends(get_db)) -> AlertRepository:
    return AlertRepository(db)


def get_election_repository(db: AsyncSession = Depends(get_db)) -> ElectionRepository:
    return ElectionRepository(db)


def get_nisanyan_repository(db: AsyncSession = Depends(get_db)) -> NisanyanRepository:
    return NisanyanRepository(db)


def get_radar_repository(db: AsyncSession = Depends(get_db)) -> RadarRepository:
    return RadarRepository(db)
