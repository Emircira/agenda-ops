from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import ComplaintsRadarCache
from app.repositories.base import BaseRepository


class ComplaintCacheRepository(BaseRepository):
    """Şikâyet radarı önbelleği (`complaints_radar_cache`)."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_by_province_key(
        self, province_key: str
    ) -> Tuple[Optional[ComplaintsRadarCache], Optional[str]]:
        """
        Satır veya şema hatası mesajı.
        ProgrammingError (tablo yok) durumunda rollback uygulanır; iş mantığı aynı kalır.
        """
        try:
            res = await self._session.execute(
                select(ComplaintsRadarCache).where(ComplaintsRadarCache.province_key == province_key)
            )
            return res.scalar_one_or_none(), None
        except ProgrammingError as e:
            await self._session.rollback()
            low = str(getattr(e, "orig", e)).lower()
            if "complaints_radar_cache" in low and (
                "does not exist" in low or "undefinedtable" in low
            ):
                return None, (
                    "Bu modül için veri katmanı henüz hazır değil. "
                    "Sistem yöneticisi yapılandırmasını tamamlamalıdır."
                )
            raise

    async def upsert_payload(
        self,
        province_key: str,
        province_label: str,
        payload: dict[str, Any],
    ) -> None:
        stmt = (
            pg_insert(ComplaintsRadarCache)
            .values(
                province_key=province_key,
                province_label=province_label,
                cached_at=datetime.utcnow(),
                payload_json=payload,
            )
            .on_conflict_do_update(
                index_elements=["province_key"],
                set_={
                    "province_label": province_label,
                    "cached_at": datetime.utcnow(),
                    "payload_json": payload,
                },
            )
        )
        await self._session.execute(stmt)
        await self._session.commit()
