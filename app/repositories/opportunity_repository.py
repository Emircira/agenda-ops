from typing import List, Sequence

from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import Opportunity
from app.repositories.base import BaseRepository


class OpportunityRepository(BaseRepository):
    """Fırsat kartları (`opportunities`)."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def delete_all(self) -> None:
        await self._session.execute(delete(Opportunity))
        await self._session.flush()

    async def list_ordered_by_recency(self, limit: int) -> List[Opportunity]:
        result = await self._session.execute(
            select(Opportunity)
            .order_by(
                desc(Opportunity.created_at),
                desc(Opportunity.score),
            )
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_top_for_daily_briefing(self, limit: int = 3) -> List[Opportunity]:
        return await self.list_ordered_by_recency(limit)

    async def persist_opportunities(self, opportunities: Sequence[Opportunity]) -> None:
        for opp in opportunities:
            self._session.add(opp)
        await self._session.commit()
