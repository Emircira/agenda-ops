"""Makro göstergeler ve anket verisi — Karargah Faz 4.1 API."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, Query

from app.models.core import MacroIndicator, PollData
from app.repositories.deps import get_macro_repository
from app.repositories.macro_repository import MacroRepository
from app.workers.macro_tasks import fetch_grievances_and_demands, fetch_real_macro_data

router = APIRouter()

KARARGAH_INSIGHT_COMPANY = "Karargah İçgörü"


def _macro_last_value_per_day(rows: List[MacroIndicator]) -> Dict[Tuple[date, str], float]:
    """Takvim günü + gösterge türü için o günün son (en yüksek id) kaydı."""
    best: Dict[Tuple[date, str], Tuple[float, int]] = {}
    for r in rows:
        if r.recorded_date is None:
            continue
        d = r.recorded_date.date() if hasattr(r.recorded_date, "date") else r.recorded_date
        k = (d, str(r.indicator_type or ""))
        rid = int(r.id or 0)
        if k not in best or rid >= best[k][1]:
            try:
                best[k] = (float(r.value), rid)
            except (TypeError, ValueError):
                continue
    return {k: v[0] for k, v in best.items()}


def _grievance_daily_aggregate(polls: List[PollData]) -> Dict[date, float]:
    """Karargah şikâyet satırları: günlük ortalama yüzde (tek çizgi)."""
    best: Dict[date, Tuple[float, int]] = {}
    for p in polls:
        raw = p.results if isinstance(p.results, dict) else {}
        nums: List[float] = []
        for v in raw.values():
            try:
                nums.append(float(v))
            except (TypeError, ValueError):
                continue
        if not nums:
            continue
        agg = round(sum(nums) / len(nums), 2)
        d = p.published_date
        rid = int(p.id or 0)
        if d not in best or rid >= best[d][1]:
            best[d] = (agg, rid)
    return {d: t[0] for d, t in best.items()}


def _date_range_labels(start: date, end: date) -> List[str]:
    out: List[str] = []
    cur = start
    while cur <= end:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def _clamp_days(days: int) -> Tuple[int, int]:
    """İstenen gün ve etkin pencere. 0 veya negatif => tümü (~3 yıl)."""
    if days <= 0:
        return 1095, 1095
    d = max(1, min(int(days), 3650))
    return d, d


def _indicator_payload(m: MacroIndicator) -> Dict[str, Any]:
    rd: datetime = m.recorded_date
    return {
        "id": m.id,
        "indicator_type": m.indicator_type,
        "value": float(m.value) if m.value is not None else None,
        "recorded_date": rd.isoformat() if rd else None,
        "source": m.source,
    }


def _poll_payload(p: PollData) -> Dict[str, Any]:
    pd: date = p.published_date
    return {
        "id": p.id,
        "company": p.company,
        "topic": p.topic,
        "type": p.poll_type,
        "results": p.results if isinstance(p.results, dict) else dict(p.results or {}),
        "published_date": pd.isoformat() if pd else None,
    }


@router.get("/dashboard-data")
async def get_macro_dashboard_data(
    macro_repo: MacroRepository = Depends(get_macro_repository),
):
    """
    Dashboard / istemci tek istekle güncel makro ve anket özetini alır.
    """
    indicators = await macro_repo.get_latest_indicators()
    polls = await macro_repo.get_latest_polls(limit=50)
    return {
        "success": True,
        "macro_indicators": [_indicator_payload(m) for m in indicators],
        "polls": [_poll_payload(p) for p in polls],
    }


@router.get("/historical-data")
async def get_macro_historical_data(
    days: int = Query(default=30, ge=0, le=3650, description="0 = geniş pencere (~1095 gün)"),
    macro_repo: MacroRepository = Depends(get_macro_repository),
):
    """
    Zaman serisi: makro göstergeler + Karargah İçgörü şikâyet özeti (grafik uyumlu).
    """
    _, eff = _clamp_days(days)
    end = datetime.utcnow().date()
    start = end - timedelta(days=max(0, eff - 1))
    since_dt = datetime(start.year, start.month, start.day)
    date_labels = _date_range_labels(start, end)

    indicators = await macro_repo.list_indicators_recorded_since_order_asc(since_dt)
    macro_map = _macro_last_value_per_day(list(indicators))

    macro_keys = (
        "inflation_tuik",
        "interest_rate",
        "unemployment",
        "inflation_enag",
    )
    macro_series: Dict[str, List[Optional[float]]] = {k: [] for k in macro_keys}
    for ds in date_labels:
        d = date.fromisoformat(ds)
        for mk in macro_keys:
            macro_series[mk].append(macro_map.get((d, mk)))

    g_polls = await macro_repo.list_polls_company_type_published_since_order_asc(
        company=KARARGAH_INSIGHT_COMPANY,
        poll_type="grievances",
        since=start,
    )
    g_map = _grievance_daily_aggregate(list(g_polls))
    grievance_values: List[Optional[float]] = []
    for ds in date_labels:
        d = date.fromisoformat(ds)
        grievance_values.append(g_map.get(d))

    return {
        "success": True,
        "days_requested": days,
        "days_effective": eff,
        "dates": date_labels,
        "macro_series": macro_series,
        "grievances_karargah": {
            "company": KARARGAH_INSIGHT_COMPANY,
            "poll_type": "grievances",
            "values": grievance_values,
        },
    }


@router.get("/force-fetch-live")
async def force_fetch_live():
    """
    Canlı makro kazıyıcı ve Karargah nabız (Gemini) işçilerini anında Celery kuyruğuna atar.
    """
    fetch_real_macro_data.delay()
    fetch_grievances_and_demands.delay()
    return {
        "status": "success",
        "message": "Canlı veri avcıları (İşçiler) sahaya sürüldü. Lütfen 1-2 dakika sonra sayfayı yenileyin.",
    }
