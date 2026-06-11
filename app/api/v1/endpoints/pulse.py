"""Gündem Nabızı API ucu — X/Twitter icin 'bugun ne konusuluyor, insanlar ne dusunuyor'.

LLM kullanmaz; content_labels icindeki mevcut sentiment + stance verisini toplulastırır.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import pytz
from fastapi import APIRouter, Depends, Query

from app.repositories.deps import get_pulse_repository
from app.repositories.pulse_repository import PulseRepository

router = APIRouter()


def _parse_window_hours(window: str) -> int:
    """'24h' / '48h' / '7d' -> saat. Varsayilan 24, tavan 168 (7 gun)."""
    w = (window or "").strip().lower()
    mult = 1
    if w.endswith("h"):
        w = w[:-1]
    elif w.endswith("d"):
        w = w[:-1]
        mult = 24
    try:
        n = int(w)
    except ValueError:
        n, mult = 24, 1
    return max(1, min(n * mult, 168))


def _mood_label(avg: Optional[float]) -> str:
    """Ortalama sentiment skorunu kitle ruh haline cevirir."""
    if avg is None:
        return "nötr"
    if avg >= 0.25:
        return "olumlu"
    if avg <= -0.25:
        return "olumsuz"
    return "karışık"


@router.get("/agenda")
async def get_agenda_pulse(
    window: str = Query("24h", description="Zaman penceresi: 24h, 48h, 7d"),
    limit: int = Query(8, ge=1, le=20),
    min_mentions: int = Query(1, ge=1, le=50),
    pulse_repo: PulseRepository = Depends(get_pulse_repository),
):
    """X/Twitter'da secilen pencerede en cok konusulan konular + kitle duygusu/tutumu."""
    hours = _parse_window_hours(window)
    since = datetime.utcnow() - timedelta(hours=hours)

    topics = await pulse_repo.top_topics_since(
        since=since, limit=limit, min_mentions=min_mentions
    )
    topic_names = [t["topic"] for t in topics]
    samples = await pulse_repo.sample_summaries_for_topics(
        since=since, topics=topic_names
    )

    items = []
    for t in topics:
        sup = int(t.get("support_count") or 0)
        opp = int(t.get("oppose_count") or 0)
        neu = int(t.get("neutral_count") or 0)
        stance_total = (sup + opp + neu) or 1
        avg_sent_raw = t.get("avg_sentiment")
        avg_sent = float(avg_sent_raw) if avg_sent_raw is not None else None
        sample = samples.get(t["topic"], {})
        items.append(
            {
                "topic": t["topic"],
                "mention_count": int(t.get("mention_count") or 0),
                "avg_sentiment": round(avg_sent, 3) if avg_sent is not None else None,
                "mood": _mood_label(avg_sent),
                "stance": {
                    "support_pct": round(100 * sup / stance_total),
                    "oppose_pct": round(100 * opp / stance_total),
                    "neutral_pct": round(100 * neu / stance_total),
                },
                "crisis_score": int(t.get("max_crisis") or 0),
                "summary": sample.get("summary", ""),
                "sample_text": sample.get("sample_text", ""),
            }
        )

    tz = pytz.timezone("Europe/Istanbul")
    return {
        "window": window,
        "hours": hours,
        "generated_at": datetime.now(tz).isoformat(),
        "platform": "x",
        "count": len(items),
        "topics": items,
    }
