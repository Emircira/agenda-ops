"""Veri erişim katmanı (Repository pattern)."""

from app.repositories.base import BaseRepository
from app.repositories.complaint_cache_repository import ComplaintCacheRepository
from app.repositories.content_repository import ContentRepository
from app.repositories.election_repository import ElectionRepository
from app.repositories.entity_repository import EntityRepository
from app.repositories.opportunity_repository import OpportunityRepository
from app.repositories.target_repository import TargetRepository

from app.repositories.vector_repository import VectorRepository

__all__ = [
    "BaseRepository",
    "ComplaintCacheRepository",
    "ContentRepository",
    "ElectionRepository",
    "EntityRepository",
    "OpportunityRepository",
    "TargetRepository",
    "VectorRepository",
]
