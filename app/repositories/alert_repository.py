"""Sistem bildirimleri (SystemAlert)."""

from __future__ import annotations

from datetime import datetime
from typing import List, Sequence

from sqlalchemy import desc, func, select, update

from app.models.core import SystemAlert
from app.repositories.base import BaseRepository


class AlertRepository(BaseRepository):
    async def create_alert(
        self,
        *,
        alert_type: str,
        severity: str,
        message: str,
        commit: bool = True,
    ) -> SystemAlert:
        row = SystemAlert(
            alert_type=alert_type,
            severity=severity,
            message=message,
            is_read=False,
            created_at=datetime.utcnow(),
        )
        self._session.add(row)
        if commit:
            await self.commit()
            await self._session.refresh(row)
        return row

    async def list_unread_order_desc(self, limit: int = 50) -> List[SystemAlert]:
        lim = max(1, min(int(limit), 200))
        result = await self._session.execute(
            select(SystemAlert)
            .where(SystemAlert.is_read == False)
            .order_by(desc(SystemAlert.created_at), desc(SystemAlert.id))
            .limit(lim)
        )
        return list(result.scalars().all())

    async def count_unread(self) -> int:
        from sqlalchemy import func

        q = await self._session.scalar(select(func.count(SystemAlert.id)).where(SystemAlert.is_read == False))
        return int(q or 0)

    async def mark_read(self, alert_id: int) -> bool:
        res = await self._session.execute(
            update(SystemAlert)
            .where(SystemAlert.id == alert_id, SystemAlert.is_read == False)
            .values(is_read=True)
        )
        return int(res.rowcount or 0) > 0

    async def exists_by_type_message(self, *, alert_type: str, message: str) -> bool:
        """Ayni tur+mesajli bir uyari zaten var mi? (idempotent uretim icin)."""
        q = await self._session.scalar(
            select(func.count(SystemAlert.id)).where(
                SystemAlert.alert_type == alert_type,
                SystemAlert.message == message,
            )
        )
        return int(q or 0) > 0

    async def list_by_severity_since_order_desc(
        self,
        *,
        severities: Sequence[str],
        since: datetime,
        limit: int = 100,
    ) -> List[SystemAlert]:
        """Orn. high/critical uyarilari belirli bir zamandan sonrasi icin (rapor / SITREP)."""
        lim = max(1, min(int(limit), 500))
        sev_norm = tuple({(s or "").strip().lower() for s in severities if (s or "").strip()})
        if not sev_norm:
            return []
        result = await self._session.execute(
            select(SystemAlert)
            .where(
                func.lower(SystemAlert.severity).in_(sev_norm),
                SystemAlert.created_at >= since,
            )
            .order_by(desc(SystemAlert.created_at), desc(SystemAlert.id))
            .limit(lim)
        )
        return list(result.scalars().all())
