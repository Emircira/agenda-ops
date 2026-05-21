"""
Günlük durum raporu (SITREP) — ham veriyi repository katmanından derler.
Çıktı JSON-serialize edilebilir düz Python yapılarıdır.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from app.repositories.alert_repository import AlertRepository
from app.repositories.macro_repository import MacroRepository

# Makro görevleriyle aynı anahtarlar (PollData şirket/konu)
PULSE_COMPANY = "Karargah İçgörü"
PULSE_TOPIC = "Canlı Toplum Nabzı"


def _iso_dt(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.isoformat()


def _iso_date(d: Optional[date]) -> Optional[str]:
    if d is None:
        return None
    return d.isoformat()


def _pulse_slice_payload(row: Any) -> Dict[str, Any]:
    """PollData satırını şablona uygun sözlüğe çevirir."""
    results = row.results if isinstance(row.results, dict) else {}
    return {
        "id": row.id,
        "published_date": _iso_date(getattr(row, "published_date", None)),
        "poll_type": getattr(row, "poll_type", "") or "",
        "results": dict(results),
    }


async def generate_daily_sitrep(
    macro_repo: MacroRepository,
    alert_repo: AlertRepository,
) -> Dict[str, Any]:
    """
    Günlük SITREP için ham veri paketi.

    - Makro: her gösterge türü için en güncel MacroIndicator satırı.
    - Toplumsal nabız: PollData günlük tarih kullanır; ``published_date >= (UTC şimdi − 24 saat).date()``
      aralığındaki şikâyet/talep satırlarından en güncel (en yeni tarih + id) tek satır.
    - Uyarılar: son 24 saatte oluşmuş high / critical SystemAlert kayıtları.
    """
    now = datetime.utcnow()
    since_alerts = now - timedelta(hours=24)
    pulse_cutoff_date = since_alerts.date()

    indicators = await macro_repo.get_latest_indicators()
    macro_payload: List[Dict[str, Any]] = []
    for m in sorted(indicators, key=lambda x: (x.indicator_type or "").lower()):
        macro_payload.append(
            {
                "indicator_type": m.indicator_type,
                "value": float(m.value) if m.value is not None else None,
                "recorded_date": _iso_dt(m.recorded_date),
                "source": m.source or "",
            }
        )

    g_rows = await macro_repo.list_polls_company_topic_type_since(
        company=PULSE_COMPANY,
        topic=PULSE_TOPIC,
        poll_type="grievances",
        since=pulse_cutoff_date,
    )
    d_rows = await macro_repo.list_polls_company_topic_type_since(
        company=PULSE_COMPANY,
        topic=PULSE_TOPIC,
        poll_type="demands",
        since=pulse_cutoff_date,
    )

    def _pick_latest(rows: List[Any]) -> Optional[Any]:
        if not rows:
            return None
        return max(rows, key=lambda r: (r.published_date, r.id))

    g_latest = _pick_latest(g_rows)
    d_latest = _pick_latest(d_rows)

    alerts = await alert_repo.list_by_severity_since_order_desc(
        severities=("high", "critical"),
        since=since_alerts,
        limit=100,
    )
    alerts_payload = [
        {
            "id": a.id,
            "alert_type": a.alert_type,
            "severity": a.severity,
            "message": a.message,
            "is_read": bool(a.is_read),
            "created_at": _iso_dt(a.created_at),
        }
        for a in alerts
    ]

    return {
        "generated_at": _iso_dt(now),
        "generated_at_display_tr": now.strftime("%d.%m.%Y %H:%M UTC"),
        "window_hours_alerts": 24,
        "pulse_cutoff_date": _iso_date(pulse_cutoff_date),
        "pulse_note": (
            "PollData kayıtları günlük `published_date` ile saklanır; "
            "«son 24 saat» penceresi bu tarih ile yaklaşık eşlenir."
        ),
        "macro_indicators": macro_payload,
        "pulse": {
            "company": PULSE_COMPANY,
            "topic": PULSE_TOPIC,
            "grievances": _pulse_slice_payload(g_latest) if g_latest else None,
            "demands": _pulse_slice_payload(d_latest) if d_latest else None,
        },
        "critical_alerts": alerts_payload,
    }
