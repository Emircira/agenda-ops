from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import Entity, EntityRelation
from app.repositories.base import BaseRepository


class EntityRepository(BaseRepository):
    """Graph / footprint modelleri (`entities`, `entity_relations`)."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def list_entities_limit(self, limit: int = 20):
        res = await self._session.execute(select(Entity).limit(limit))
        return res.scalars().all()

    async def list_relations_limit(self, limit: int = 50):
        res = await self._session.execute(select(EntityRelation).limit(limit))
        return res.scalars().all()
