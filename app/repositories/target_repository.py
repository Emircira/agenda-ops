from typing import List, Optional, Sequence

from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import Content, Source
from app.repositories.base import BaseRepository


class TargetRepository(BaseRepository):
    """
    İzleme hedefi kaynakları (`sources` tablosu).
    API ve servislerde 'hedef kanal' anlamında kullanılır.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def create_and_refresh(self, source: Source) -> Source:
        self._session.add(source)
        await self._session.commit()
        await self._session.refresh(source)
        return source

    async def list_all(self) -> List[Source]:
        result = await self._session.execute(select(Source))
        return list(result.scalars().all())

    async def list_all_order_desc_id(self) -> List[Source]:
        res = await self._session.execute(select(Source).order_by(desc(Source.id)))
        return list(res.scalars().all())

    async def get_by_id(self, source_id: int) -> Optional[Source]:
        res = await self._session.execute(select(Source).where(Source.id == source_id))
        return res.scalar_one_or_none()

    async def add_and_refresh(self, source: Source) -> Source:
        return await self.create_and_refresh(source)

    async def delete_source_cascade(self, source: Source) -> None:
        sid = source.id
        await self._session.execute(delete(Content).where(Content.source_id == sid))
        await self._session.delete(source)

    # --- Worker / run-worker source projections ---
    async def list_active_rss_maps(self) -> List[dict]:
        result = await self._session.execute(
            select(Source.id, Source.name, Source.url, Source.domain).where(
                Source.type == "rss", Source.active == True
            )
        )
        return [dict(row) for row in result.mappings().all()]

    async def list_active_youtube_maps(self) -> List[dict]:
        result = await self._session.execute(
            select(Source.id, Source.name, Source.url, Source.domain).where(
                Source.type == "youtube", Source.active == True
            )
        )
        return [dict(row) for row in result.mappings().all()]

    async def list_active_x_maps(self) -> List[dict]:
        result = await self._session.execute(
            select(Source.id, Source.type, Source.name, Source.url, Source.domain).where(
                Source.type.in_(
                    ["twitter_self", "twitter_competitor", "twitter_trend", "twitter_agency", "x"]
                ),
                Source.active == True,
            )
        )
        return [dict(row) for row in result.mappings().all()]

    async def count_active_sources(self) -> int:
        return int(
            (await self._session.scalar(select(func.count(Source.id)).where(Source.active == True)))
            or 0
        )
