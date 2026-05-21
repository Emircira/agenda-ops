"""Nişanyan / Index Anatolicus önbellek satırları."""

from __future__ import annotations

from typing import List, Sequence

from sqlalchemy import delete, select

from app.models.core import NisanyanDemographics
from app.repositories.base import BaseRepository


class NisanyanRepository(BaseRepository):
    async def list_by_province_district(self, province: str, district: str) -> List[NisanyanDemographics]:
        dkey = (district or "").strip()
        res = await self._session.execute(
            select(NisanyanDemographics)
            .where(
                NisanyanDemographics.province == province,
                NisanyanDemographics.district == dkey,
            )
            .order_by(NisanyanDemographics.settlement_name, NisanyanDemographics.id)
        )
        return list(res.scalars().all())

    async def replace_district_cache(
        self,
        *,
        province: str,
        district: str,
        rows: Sequence[NisanyanDemographics],
        commit: bool = True,
    ) -> None:
        dkey = (district or "").strip()
        await self._session.execute(
            delete(NisanyanDemographics).where(
                NisanyanDemographics.province == province,
                NisanyanDemographics.district == dkey,
            )
        )
        for r in rows:
            self._session.add(r)
        if commit:
            await self.commit()
