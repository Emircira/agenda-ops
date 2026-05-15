from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional
from uuid import UUID

from sqlalchemy import and_, delete, desc, func, not_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.core import Content, ContentLabel, Source
from app.repositories.base import BaseRepository


class ContentRepository(BaseRepository):
    """İçerik (contents) ve ilişkili sorgular — Karargah veri erişimi."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def list_recent_with_labels(self, limit: int = 300):
        result = await self._session.execute(
            select(Content)
            .options(joinedload(Content.labels))
            .order_by(Content.id.desc())
            .limit(limit)
        )
        return result.scalars().unique().all()

    async def get_by_id(self, content_id: UUID) -> Optional[Content]:
        result = await self._session.execute(select(Content).where(Content.id == content_id))
        return result.scalar_one_or_none()

    async def list_labeled_content_pairs_since(self, since: datetime):
        query = (
            select(Content, ContentLabel)
            .join(ContentLabel, Content.id == ContentLabel.content_id)
            .where(Content.published_at >= since)
        )
        res = await self._session.execute(query)
        return res.all()

    # --- Ingest / upsert ---
    async def insert_many_ignore_conflict(self, rows: List[dict]) -> int:
        if not rows:
            return 0
        stmt = pg_insert(Content).values(rows).on_conflict_do_nothing(index_elements=["external_id"])
        res = await self._session.execute(stmt)
        return int(res.rowcount or 0)

    async def insert_one_ignore_conflict(self, values: dict) -> int:
        stmt = (
            pg_insert(Content)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["external_id"])
        )
        res = await self._session.execute(stmt)
        return int(res.rowcount or 0)

    async def insert_one_ignore_conflict_commit(self, values: dict) -> int:
        n = await self.insert_one_ignore_conflict(values)
        await self.commit()
        return n

    # --- Cleanup ---
    async def delete_published_before_with_external_guard(
        self, cutoff: datetime, external_id_not_ilike: str
    ) -> int:
        stmt = delete(Content).where(
            Content.published_at < cutoff,
            not_(Content.external_id.ilike(external_id_not_ilike)),
        )
        res = await self._session.execute(stmt)
        return int(res.rowcount or 0)

    async def delete_by_source_id(self, source_id: int) -> None:
        await self._session.execute(delete(Content).where(Content.source_id == source_id))

    # --- Triage (unlabeled) ---
    async def fetch_unlabeled_by_fetched_since(self, cutoff: datetime) -> List[Content]:
        stmt = (
            select(Content)
            .outerjoin(ContentLabel, Content.id == ContentLabel.content_id)
            .where(ContentLabel.content_id.is_(None), Content.fetched_at >= cutoff)
        )
        res = await self._session.execute(stmt)
        return list(res.scalars().all())

    # --- Labeling batches ---
    async def fetch_unanalyzed_with_source_category(self, limit: int) -> List[Any]:
        stmt = (
            select(Content, Source.source_category)
            .outerjoin(Source, Content.source_id == Source.id)
            .where(Content.is_analyzed == False)
            .order_by(Content.published_at.asc())
            .limit(limit)
        )
        res = await self._session.execute(stmt)
        return res.all()

    async def fetch_unanalyzed_twitter_with_source_category(self, limit: int) -> List[Any]:
        stmt = (
            select(Content, Source.source_category)
            .outerjoin(Source, Content.source_id == Source.id)
            .where(and_(Content.is_analyzed == False, Content.platform == "twitter"))
            .order_by(Content.published_at.asc())
            .limit(limit)
        )
        res = await self._session.execute(stmt)
        return res.all()

    async def fetch_unanalyzed_youtube_comment_with_source_category(
        self, limit: int
    ) -> List[Any]:
        stmt = (
            select(Content, Source.source_category)
            .outerjoin(Source, Content.source_id == Source.id)
            .where(and_(Content.is_analyzed == False, Content.platform == "youtube_comment"))
            .order_by(Content.published_at.asc())
            .limit(limit)
        )
        res = await self._session.execute(stmt)
        return res.all()

    async def merge_label(self, label: ContentLabel) -> ContentLabel:
        return await self._session.merge(label)

    async def add_content(self, row: Content) -> None:
        self._session.add(row)

    # --- Dashboard / API reads ---
    async def list_published_since_order_desc(self, time_threshold: datetime, limit: int) -> List[Content]:
        res = await self._session.execute(
            select(Content)
            .where(Content.published_at >= time_threshold)
            .order_by(desc(Content.published_at))
            .limit(limit)
        )
        return list(res.scalars().all())

    async def get_latest_poll_content(self) -> Optional[Content]:
        res = await self._session.execute(
            select(Content)
            .where(Content.platform == "poll")
            .order_by(desc(Content.published_at))
            .limit(1)
        )
        return res.scalar_one_or_none()

    async def list_hot_topics_candidates(self, time_threshold: datetime, limit: int = 50) -> List[Content]:
        res = await self._session.execute(
            select(Content)
            .where(Content.published_at >= time_threshold)
            .order_by(desc(Content.published_at), desc(Content.fetched_at))
            .limit(limit)
        )
        return list(res.scalars().all())

    async def fetch_x_contents_for_bulk_report(
        self, time_threshold: datetime, limit: int = 500
    ) -> List[Any]:
        query = (
            select(Content, Source.type, Source.source_category)
            .outerjoin(Source, Content.source_id == Source.id)
            .where(
                Content.platform.in_(["x", "twitter"]),
                Content.published_at >= time_threshold,
            )
            .order_by(Content.published_at.desc())
            .limit(limit)
        )
        res = await self._session.execute(query)
        return res.all()

    async def fetch_triple_compare_rows(self, time_threshold: datetime) -> List[Any]:
        query = (
            select(Content, Source.type, Source.source_category)
            .join(Source, Content.source_id == Source.id)
            .where(
                Content.platform.in_(["x", "twitter"]),
                Content.published_at >= time_threshold,
            )
        )
        res = await self._session.execute(query)
        return res.all()

    async def list_published_since_limit(self, time_threshold: datetime, limit: int) -> List[Content]:
        res = await self._session.execute(
            select(Content).where(Content.published_at >= time_threshold).limit(limit)
        )
        return list(res.scalars().all())

    async def search_platform_text_ilike(
        self, platform_name: str, pattern: str, limit: int = 50
    ) -> List[Content]:
        res = await self._session.execute(
            select(Content).where(
                Content.platform == platform_name,
                Content.text.ilike(pattern),
            ).limit(limit)
        )
        return list(res.scalars().all())

    async def list_rss_since(self, time_limit: datetime) -> List[Content]:
        res = await self._session.execute(
            select(Content).where(
                Content.published_at >= time_limit,
                Content.platform == "rss",
            )
        )
        return list(res.scalars().all())

    # --- Volume stats ---
    async def count_by_platform_since(self, time_threshold: datetime) -> List[Any]:
        res = await self._session.execute(
            select(Content.platform, func.count(Content.id).label("cnt"))
            .where(Content.published_at >= time_threshold)
            .group_by(Content.platform)
        )
        return res.all()

    async def count_by_platform_in_slot(
        self, slot_start: datetime, slot_end: datetime
    ) -> List[Any]:
        res = await self._session.execute(
            select(Content.platform, func.count(Content.id))
            .where(Content.published_at >= slot_start, Content.published_at < slot_end)
            .group_by(Content.platform)
        )
        return res.all()

    # --- Stream ---
    async def list_by_platforms_order_fetched(
        self, platforms: List[str], limit: int
    ) -> List[Content]:
        res = await self._session.execute(
            select(Content)
            .where(Content.platform.in_(platforms))
            .order_by(desc(Content.fetched_at), desc(Content.published_at))
            .limit(limit)
        )
        return list(res.scalars().all())

    # --- Deep research RSS db sources ---
    async def list_active_rss_sources(self) -> List[Source]:
        res = await self._session.execute(
            select(Source).where(Source.type == "rss", Source.active == True)
        )
        return list(res.scalars().all())

    # --- OSINT ---
    async def osint_label_aggregates_since(self, time_threshold: datetime) -> Any:
        query = (
            select(
                func.avg(ContentLabel.sentiment_score).label("avg_sentiment"),
                func.avg(ContentLabel.manipulation_prob).label("avg_manipulation"),
                func.avg(ContentLabel.bot_likelihood).label("avg_bot"),
                func.count(ContentLabel.content_id).label("total_labeled"),
            )
            .join(Content, Content.id == ContentLabel.content_id)
            .where(Content.published_at >= time_threshold)
        )
        res = await self._session.execute(query)
        return res.one()

    async def osint_sarcasm_count_since(self, time_threshold: datetime) -> int:
        sarcasm_query = (
            select(func.count(ContentLabel.content_id))
            .where(ContentLabel.sarcasm_detected == True)
            .join(Content, Content.id == ContentLabel.content_id)
            .where(Content.published_at >= time_threshold)
        )
        return int((await self._session.execute(sarcasm_query)).scalar() or 0)

    async def fetch_twitter_raw_json_since(self, time_threshold: datetime, limit: int = 1000):
        res = await self._session.execute(
            select(Content.raw_json)
            .where(Content.platform == "twitter", Content.published_at >= time_threshold)
            .limit(limit)
        )
        return res.all()

    # --- System stats ---
    async def count_all(self) -> int:
        return int((await self._session.scalar(select(func.count(Content.id)))) or 0)

    async def count_published_since(self, time_threshold: datetime) -> int:
        return int(
            (
                await self._session.scalar(
                    select(func.count(Content.id)).where(Content.published_at >= time_threshold)
                )
            )
            or 0
        )

    async def group_count_by_platform(self) -> List[Any]:
        res = await self._session.execute(
            select(Content.platform, func.count(Content.id)).group_by(Content.platform)
        )
        return res.all()

    async def list_recent_by_fetched_limit(self, limit: int = 10) -> List[Content]:
        res = await self._session.execute(
            select(Content).order_by(desc(Content.fetched_at)).limit(limit)
        )
        return list(res.scalars().all())
