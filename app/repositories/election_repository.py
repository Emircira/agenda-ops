from typing import List, Optional, Sequence

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import (
    DistrictDemographics,
    ElectionDemographicStat,
    ElectionResult,
    RegionAnalysis,
)
from app.repositories.base import BaseRepository


class ElectionRepository(BaseRepository):
    """Seçim radarı ve arşiv tabloları."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def list_districts_by_province(self, province: str) -> List[DistrictDemographics]:
        res = await self._session.execute(
            select(DistrictDemographics).where(DistrictDemographics.province == province)
        )
        return list(res.scalars().all())

    async def list_all_district_demographics(self) -> List[DistrictDemographics]:
        res = await self._session.execute(select(DistrictDemographics))
        return list(res.scalars().all())

    async def list_election_results_by_type(self, category) -> List[ElectionResult]:
        res = await self._session.execute(
            select(ElectionResult).where(ElectionResult.election_type == category)
        )
        return list(res.scalars().all())

    async def list_wikipedia_fallback_results(self, category, year: int) -> List[ElectionResult]:
        res = await self._session.execute(
            select(ElectionResult).where(
                ElectionResult.election_type == category,
                ElectionResult.election_year == year,
                ElectionResult.source_json_file.ilike("wikipedia%"),
            )
        )
        return list(res.scalars().all())

    async def add_election_results(self, rows: Sequence[ElectionResult]) -> None:
        for r in rows:
            self._session.add(r)

    async def list_demographic_stats_by_type(self, category) -> List[ElectionDemographicStat]:
        res = await self._session.execute(
            select(ElectionDemographicStat).where(ElectionDemographicStat.election_type == category)
        )
        return list(res.scalars().all())

    async def get_latest_region_analysis_cache(
        self,
        province: str,
        election_year: int,
        neighborhood: str,
        district_key: Optional[str],
    ) -> Optional[RegionAnalysis]:
        cache_query = select(RegionAnalysis).where(
            RegionAnalysis.province == province,
            RegionAnalysis.election_year == election_year,
            RegionAnalysis.neighborhood == neighborhood,
        )
        if district_key:
            cache_query = cache_query.where(RegionAnalysis.district == district_key)
        else:
            cache_query = cache_query.where(
                (RegionAnalysis.district == None) | (RegionAnalysis.district == "")
            )
        res = await self._session.execute(
            cache_query.order_by(desc(RegionAnalysis.last_analyzed_at)).limit(1)
        )
        return res.scalar_one_or_none()

    async def add_region_analysis(self, row: RegionAnalysis) -> None:
        self._session.add(row)
