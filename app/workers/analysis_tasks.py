"""
Anomali tespiti — makro ve toplumsal nabız (PollData) yazımından sonra tetiklenir.
Harici API yok; SystemAlert üzerinden dashboard bildirimi.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.alert_repository import AlertRepository
from app.repositories.macro_repository import MacroRepository

KARARGAH_COMPANY = "Karargah İçgörü"
GRIEVANCE_TOPIC_LIVE = "Canlı Toplum Nabzı"


def _macro_severity(rel_change: float) -> str:
    a = abs(rel_change)
    if a >= 0.25:
        return "critical"
    if a >= 0.10:
        return "high"
    return "medium"


def _pct_delta(curr: float, baseline: float) -> float:
    if baseline <= 0:
        return 0.0
    return (curr - baseline) / baseline


async def check_for_anomalies_after_macro_writes(
    db: AsyncSession,
    pending: Sequence[Tuple[str, float, Optional[float]]],
) -> None:
    """
    pending: (indicator_type, new_value, previous_value).
    Önceki değere göre göreli |fark| >= %5 ise uyarı (macro_spike).
    """
    if not pending:
        return
    alerts = AlertRepository(db)
    try:
        for ind_type, new_val, prev_val in pending:
            if prev_val is None:
                continue
            pv = float(prev_val)
            nv = float(new_val)
            base = abs(pv) if abs(pv) > 1e-9 else max(abs(nv), 1e-9)
            rel = abs(nv - pv) / base
            if rel < 0.05:
                continue
            sev = _macro_severity(rel)
            pct_diff = round(rel * 100.0, 1)
            msg = (
                f"Makro anomalisi: [{ind_type}] {pv:g} → {nv:g} "
                f"(göreli fark yaklaşık %{pct_diff})."
            )
            await alerts.create_alert(
                alert_type="macro_spike",
                severity=sev,
                message=msg,
                commit=False,
            )
            logger.info(f"Anomali uyarısı yazıldı (macro_spike / {sev}): {ind_type}")
        await alerts.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Makro anomali kontrolü atlandı: {e}")
        await db.rollback()


def _poll_numeric(results: Any, key: str) -> Optional[float]:
    if not isinstance(results, dict):
        return None
    raw = results.get(key)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


async def check_for_anomalies_after_grievance_write(
    db: AsyncSession,
    *,
    company: str,
    topic: str,
    latest_results: Dict[str, float],
) -> None:
    """
    Son yazılan şikâyet dağılımı vs. önceki kayıtların (aynı oturum hariç) 7 günlük ortalaması.
    Ortalamadan %15+ yüksekse grievance_spike (high).
    """
    if not latest_results:
        return
    alerts = AlertRepository(db)
    macro = MacroRepository(db)
    try:
        since = date.today() - timedelta(days=7)
        rows = await macro.list_polls_company_topic_type_since(
            company=company,
            topic=topic,
            poll_type="grievances",
            since=since,
        )
        if len(rows) < 2:
            return
        latest_id = max(r.id for r in rows)
        hist = [r for r in rows if r.id != latest_id]
        if not hist:
            return

        for label, curr_v in latest_results.items():
            vals: List[float] = []
            for r in hist:
                v = _poll_numeric(r.results, label)
                if v is not None:
                    vals.append(v)
            if not vals:
                continue
            avg = sum(vals) / len(vals)
            delta_rel = _pct_delta(float(curr_v), avg)
            if delta_rel < 0.15:
                continue
            pct_jump = round(delta_rel * 100.0, 1)
            curr_pct = round(float(curr_v), 1)
            avg_pct = round(avg, 1)
            msg = (
                f"Anomali Tespit Edildi: [{label}] taleplerinde ani artış (%{pct_jump}). "
                f"(Son 7 gün ortalaması %{avg_pct}, güncel %{curr_pct}.)"
            )
            await alerts.create_alert(
                alert_type="grievance_spike",
                severity="high",
                message=msg,
                commit=False,
            )
            logger.info(f"Anomali uyarısı yazıldı (grievance_spike): {label}")
        await alerts.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Şikâyet anomali kontrolü atlandı: {e}")
        await db.rollback()
