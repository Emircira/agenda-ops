"""İstihbarat kaynakları (`sources` / IntelligenceSource) — CRUD ve işçi projeksiyonları."""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import Content, IntelligenceSource
from app.repositories.base import BaseRepository


class SourceRepository(BaseRepository):
    async def create_and_refresh(self, source: IntelligenceSource) -> IntelligenceSource:
        self._session.add(source)
        await self._session.commit()
        await self._session.refresh(source)
        return source

    async def add_and_refresh(self, source: IntelligenceSource) -> IntelligenceSource:
        return await self.create_and_refresh(source)

    async def list_all(self) -> List[IntelligenceSource]:
        result = await self._session.execute(select(IntelligenceSource))
        return list(result.scalars().all())

    async def list_all_order_desc_id(self) -> List[IntelligenceSource]:
        res = await self._session.execute(select(IntelligenceSource).order_by(desc(IntelligenceSource.id)))
        return list(res.scalars().all())

    async def get_by_id(self, source_id: int) -> Optional[IntelligenceSource]:
        res = await self._session.execute(select(IntelligenceSource).where(IntelligenceSource.id == source_id))
        return res.scalar_one_or_none()

    async def delete_source_cascade(self, source: IntelligenceSource) -> None:
        sid = source.id
        await self._session.execute(delete(Content).where(Content.source_id == sid))
        await self._session.delete(source)

    async def delete_source_cascade_by_id(self, source_id: int) -> bool:
        source = await self.get_by_id(source_id)
        if not source:
            return False
        await self.delete_source_cascade(source)
        await self.commit()
        return True

    async def touch_last_fetched(self, source_id: int, *, commit: bool = False) -> None:
        row = await self.get_by_id(source_id)
        if row is None:
            return
        row.last_fetched_at = datetime.utcnow()
        if commit:
            await self.commit()
        else:
            await self._session.flush()

    # --- Worker projeksiyonları (is_active=True) ---
    async def list_active_rss_maps(self) -> List[Dict]:
        result = await self._session.execute(
            select(IntelligenceSource.id, IntelligenceSource.name, IntelligenceSource.url, IntelligenceSource.domain).where(
                IntelligenceSource.type == "rss",
                IntelligenceSource.active == True,
            )
        )
        return [dict(row) for row in result.mappings().all()]

    async def list_active_youtube_maps(self) -> List[Dict]:
        result = await self._session.execute(
            select(IntelligenceSource.id, IntelligenceSource.name, IntelligenceSource.url, IntelligenceSource.domain).where(
                IntelligenceSource.type == "youtube",
                IntelligenceSource.active == True,
            )
        )
        return [dict(row) for row in result.mappings().all()]

    async def list_active_x_maps(self) -> List[Dict]:
        result = await self._session.execute(
            select(IntelligenceSource.id, IntelligenceSource.type, IntelligenceSource.name, IntelligenceSource.url, IntelligenceSource.domain).where(
                IntelligenceSource.type.in_(
                    [
                        "twitter_self",
                        "twitter_competitor",
                        "twitter_trend",
                        "twitter_agency",
                        "x",
                        "twitter",
                    ]
                ),
                IntelligenceSource.active == True,
            )
        )
        return [dict(row) for row in result.mappings().all()]

    async def count_active_sources(self) -> int:
        return int(
            (await self._session.scalar(select(func.count(IntelligenceSource.id)).where(IntelligenceSource.active == True)))
            or 0
        )
