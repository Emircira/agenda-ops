"""
Gunluk durum raporu (SITREP) - ham veriyi repository katmanindan derler.
Cikti JSON-serialize edilebilir duz Python yapilaridir.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import pytz

from app.repositories.alert_repository import AlertRepository
from app.repositories.macro_repository import MacroRepository
from app.repositories.radar_repository import RadarRepository

# Makro gorevleriyle ayni anahtarlar (PollData sirket/konu)
PULSE_COMPANY = "Karargah Icgoru"
PULSE_TOPIC = "Canli Toplum Nabzi"

# Radar SITREP penceresi
RADAR_SITREP_WINDOW_DAYS = 7
RADAR_SITREP_LIMIT = 5


def _iso_dt(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.isoformat()


def _iso_date(d: Optional[date]) -> Optional[str]:
    if d is None:
        return None
    return d.isoformat()


def _pulse_slice_payload(row: Any) -> Dict[str, Any]:
    """PollData satirini sablona uygun sozluge cevirir."""
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
    radar_repo: Optional[RadarRepository] = None,
) -> Dict[str, Any]:
    """
    Gunluk SITREP icin ham veri paketi.

    - Makro: her gosterge turu icin en guncel MacroIndicator satiri.
    - Toplumsal nabiz: PollData gunluk tarih kullanir.
    - Uyarilar: son 24 saatte olusmus high / critical SystemAlert kayitlari.
    - Radar: son 7 gunun en yuksek skorlu hesaplari (radar_repo verilirse).
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

    radar_top_movers: List[Dict[str, Any]] = []
    if radar_repo is not None:
        try:
            ist = pytz.timezone("Europe/Istanbul")
            today_ist = datetime.now(ist).date()
            start_day = today_ist - timedelta(days=RADAR_SITREP_WINDOW_DAYS - 1)
            rows = await radar_repo.leaderboard(
                start_day=start_day,
                platform=None,
                limit=RADAR_SITREP_LIMIT,
                order="score",
            )
            for i, r in enumerate(rows, 1):
                radar_top_movers.append(
                    {
                        "rank": i,
                        "platform": r.get("platform"),
                        "username": r.get("username"),
                        "radar_score": round(float(r.get("total_score") or 0), 2),
                        "posts": int(r.get("total_posts") or 0),
                        "comments_received": int(r.get("total_comments") or 0),
                        "engagement": int(r.get("total_engagement") or 0),
                        "reach_estimate": int(r.get("total_reach") or 0),
                        "streak_days": int(r.get("max_streak") or 0),
                    }
                )
        except Exception:
            radar_top_movers = []

    return {
        "generated_at": _iso_dt(now),
        "generated_at_display_tr": now.strftime("%d.%m.%Y %H:%M UTC"),
        "window_hours_alerts": 24,
        "pulse_cutoff_date": _iso_date(pulse_cutoff_date),
        "pulse_note": (
            "PollData kayitlari gunluk `published_date` ile saklanir; "
            "<<son 24 saat>> penceresi bu tarih ile yaklasik eslenir."
        ),
        "macro_indicators": macro_payload,
        "pulse": {
            "company": PULSE_COMPANY,
            "topic": PULSE_TOPIC,
            "grievances": _pulse_slice_payload(g_latest) if g_latest else None,
            "demands": _pulse_slice_payload(d_latest) if d_latest else None,
        },
        "critical_alerts": alerts_payload,
        "radar": {
            "window_days": RADAR_SITREP_WINDOW_DAYS,
            "top_movers": radar_top_movers,
        },
    }
