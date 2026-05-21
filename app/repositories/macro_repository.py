"""Makro göstergeler ve anket verisi — okuma katmanı."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import asc, desc, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import MacroIndicator, PollData
from app.repositories.base import BaseRepository


class MacroRepository(BaseRepository):
    """MacroIndicator / PollData sorguları."""

    async def get_latest_indicators(self) -> List[MacroIndicator]:
        """
        Her indicator_type için en güncel (recorded_date) tek satırı döner.
        """
        result = await self._session.execute(
            select(MacroIndicator).order_by(
                desc(MacroIndicator.recorded_date),
                desc(MacroIndicator.id),
            )
        )
        rows: Sequence[MacroIndicator] = result.scalars().all()
        seen: set[str] = set()
        latest: List[MacroIndicator] = []
        for row in rows:
            key = row.indicator_type
            if key in seen:
                continue
            seen.add(key)
            latest.append(row)
        return latest

    async def get_latest_polls(self, limit: int = 50) -> List[PollData]:
        """Yayın tarihine göre en yeni anketler."""
        lim = max(1, min(int(limit), 200))
        result = await self._session.execute(
            select(PollData)
            .order_by(desc(PollData.published_date), desc(PollData.id))
            .limit(lim)
        )
        return list(result.scalars().all())

    async def get_latest_indicator_for_type(self, indicator_type: str) -> Optional[MacroIndicator]:
        """Belirtilen gösterge türü için en güncel tek satır."""
        result = await self._session.execute(
            select(MacroIndicator)
            .where(MacroIndicator.indicator_type == indicator_type)
            .order_by(desc(MacroIndicator.recorded_date), desc(MacroIndicator.id))
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_latest_poll_row(
        self,
        *,
        company: str,
        topic: str,
        poll_type: str,
    ) -> Optional[PollData]:
        """Şirket + konu + tip için en güncel PollData satırı (yinelenen yazım kontrolü)."""
        result = await self._session.execute(
            select(PollData)
            .where(
                PollData.company == company,
                PollData.topic == topic,
                PollData.poll_type == poll_type,
            )
            .order_by(desc(PollData.published_date), desc(PollData.id))
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_indicators_recorded_since_order_asc(self, since: datetime) -> List[MacroIndicator]:
        """Tarihsel makro: recorded_date >= since, eskiden yeniye."""
        result = await self._session.execute(
            select(MacroIndicator)
            .where(MacroIndicator.recorded_date >= since)
            .order_by(asc(MacroIndicator.recorded_date), asc(MacroIndicator.id))
        )
        return list(result.scalars().all())

    async def list_polls_company_type_published_since_order_asc(
        self,
        *,
        company: str,
        poll_type: str,
        since: date,
    ) -> List[PollData]:
        """Tarihsel anket: şirket + tip + published_date >= since, eskiden yeniye."""
        result = await self._session.execute(
            select(PollData)
            .where(
                PollData.company == company,
                PollData.poll_type == poll_type,
                PollData.published_date >= since,
            )
            .order_by(asc(PollData.published_date), asc(PollData.id))
        )
        return list(result.scalars().all())

    async def list_polls_company_topic_type_since(
        self,
        *,
        company: str,
        topic: str,
        poll_type: str,
        since: date,
    ) -> List[PollData]:
        """Şirket + konu + tip için published_date >= since anketleri (eskiden yeniye)."""
        result = await self._session.execute(
            select(PollData)
            .where(
                PollData.company == company,
                PollData.topic == topic,
                PollData.poll_type == poll_type,
                PollData.published_date >= since,
            )
            .order_by(asc(PollData.published_date), asc(PollData.id))
        )
        return list(result.scalars().all())

    async def delete_poll_data_published_before(self, cutoff: date) -> int:
        """Retention: published_date < cutoff (tarih) olan anket / şikâyet satırlarını siler."""
        res = await self._session.execute(delete(PollData).where(PollData.published_date < cutoff))
        return int(res.rowcount or 0)

    async def add_indicator(
        self,
        *,
        indicator_type: str,
        value: float,
        recorded_date: datetime,
        source: str,
        commit: bool = True,
    ) -> MacroIndicator:
        row = MacroIndicator(
            indicator_type=indicator_type,
            value=value,
            recorded_date=recorded_date,
            source=source,
        )
        self._session.add(row)
        if commit:
            await self.commit()
            await self._session.refresh(row)
        return row

    async def add_poll(
        self,
        *,
        company: str,
        topic: str,
        results: Dict[str, Any],
        published_date: date,
        poll_type: str = "grievances",
        commit: bool = True,
    ) -> PollData:
        row = PollData(
            company=company,
            topic=topic,
            poll_type=poll_type,
            results=results,
            published_date=published_date,
        )
        self._session.add(row)
        if commit:
            await self.commit()
            await self._session.refresh(row)
        return row
