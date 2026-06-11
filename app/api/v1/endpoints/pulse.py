"""Gündem Nabızı + Gündem İçgörüleri API uclari — X/Twitter.

LLM kullanmaz; content_labels icindeki mevcut topic / sentiment / stance /
frame verisini istatistiksel olarak toplulastırır.

Uclar:
- GET /agenda        : bugun ne konusuluyor + kitle duygusu/tutumu
- GET /momentum      : yukselen/dusen gundem (mevcut vs onceki pencere + sparkline)
- GET /polarization  : kutuplasma/catisma haritasi (support vs oppose dengesi)
- GET /frames        : naratif/cerceve savasi (konu basina frame dagilimi)
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

import pytz
from fastapi import APIRouter, Depends, Query

from app.repositories.deps import get_pulse_repository
from app.repositories.pulse_repository import PulseRepository

router = APIRouter()

_IST = pytz.timezone("Europe/Istanbul")


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

    return {
        "window": window,
        "hours": hours,
        "generated_at": datetime.now(_IST).isoformat(),
        "platform": "x",
        "count": len(items),
        "topics": items,
    }


@router.get("/momentum")
async def get_momentum(
    window: str = Query("24h", description="Zaman penceresi: 24h, 48h, 7d"),
    limit: int = Query(8, ge=1, le=20),
    pulse_repo: PulseRepository = Depends(get_pulse_repository),
):
    """Yukselen/dusen gundem: mevcut pencere ile onceki ayni uzunluktaki
    pencereyi karsilastirir; her konu icin degisim yuzdesi + saatlik sparkline.
    """
    hours = _parse_window_hours(window)
    now = datetime.utcnow()
    cur_start = now - timedelta(hours=hours)
    prev_start = now - timedelta(hours=2 * hours)

    cur = await pulse_repo.topic_counts_between(start=cur_start, end=now)
    prev = await pulse_repo.topic_counts_between(start=prev_start, end=cur_start)
    prev_map = {r["topic"]: int(r["mention_count"]) for r in prev}

    cur_sorted = sorted(
        cur, key=lambda r: int(r["mention_count"]), reverse=True
    )[:limit]
    topic_names = [r["topic"] for r in cur_sorted]

    hourly = await pulse_repo.topic_hourly_counts(
        since=cur_start, topics=topic_names
    )
    # topic -> {saat_prefix: cnt}
    hmap: dict = defaultdict(lambda: defaultdict(int))
    for row in hourly:
        pref = str(row["bucket"])[:13]  # YYYY-MM-DDTHH
        hmap[row["topic"]][pref] += int(row["cnt"])

    # saat ekseni (cur_start -> now)
    axis = []
    t = cur_start.replace(minute=0, second=0, microsecond=0)
    end_axis = now.replace(minute=0, second=0, microsecond=0)
    while t <= end_axis:
        axis.append(t.isoformat()[:13])
        t += timedelta(hours=1)

    items = []
    for r in cur_sorted:
        topic = r["topic"]
        cur_c = int(r["mention_count"])
        prev_c = prev_map.get(topic, 0)
        if prev_c == 0:
            trend = "new" if cur_c > 0 else "flat"
            delta_pct = None
        else:
            delta_pct = round((cur_c - prev_c) / prev_c * 100)
            if delta_pct > 10:
                trend = "up"
            elif delta_pct < -10:
                trend = "down"
            else:
                trend = "flat"
        spark = [hmap[topic].get(p, 0) for p in axis]
        items.append(
            {
                "topic": topic,
                "current": cur_c,
                "previous": prev_c,
                "delta_pct": delta_pct,
                "trend": trend,
                "spark": spark,
            }
        )

    return {
        "window": window,
        "hours": hours,
        "generated_at": datetime.now(_IST).isoformat(),
        "count": len(items),
        "topics": items,
    }


@router.get("/polarization")
async def get_polarization(
    window: str = Query("24h", description="Zaman penceresi: 24h, 48h, 7d"),
    limit: int = Query(8, ge=1, le=20),
    pulse_repo: PulseRepository = Depends(get_pulse_repository),
):
    """Kutuplasma/catisma haritasi: support ile oppose'un en dengeli (en cok
    bolunmus) oldugu konulari one cikarir. polarization 0-100.
    """
    hours = _parse_window_hours(window)
    since = datetime.utcnow() - timedelta(hours=hours)

    rows = await pulse_repo.top_topics_since(
        since=since, limit=max(limit * 3, limit), min_mentions=3
    )

    items = []
    for t in rows:
        sup = int(t.get("support_count") or 0)
        opp = int(t.get("oppose_count") or 0)
        neu = int(t.get("neutral_count") or 0)
        total = sup + opp + neu
        # gercek bir 'catisma' icin iki taraf da temsil edilmeli
        if sup == 0 or opp == 0 or total == 0:
            continue
        so = sup + opp
        balance = 1 - abs(sup - opp) / so  # 1.0 = tam ortadan bolunmus
        # tutum alanlarin oranina gore agirliklandir (gurultuyu bastir)
        polarization = round(100 * balance * (so / total))
        items.append(
            {
                "topic": t["topic"],
                "mention_count": int(t.get("mention_count") or 0),
                "support": sup,
                "oppose": opp,
                "neutral": neu,
                "support_pct": round(100 * sup / total),
                "oppose_pct": round(100 * opp / total),
                "neutral_pct": round(100 * neu / total),
                "polarization": polarization,
            }
        )

    items.sort(key=lambda x: (x["polarization"], x["mention_count"]), reverse=True)
    items = items[:limit]

    return {
        "window": window,
        "hours": hours,
        "generated_at": datetime.now(_IST).isoformat(),
        "count": len(items),
        "topics": items,
    }


@router.get("/frames")
async def get_frames(
    window: str = Query("24h", description="Zaman penceresi: 24h, 48h, 7d"),
    limit: int = Query(6, ge=1, le=20),
    pulse_repo: PulseRepository = Depends(get_pulse_repository),
):
    """Naratif/Cerceve savasi: en cok konusulan konularin hangi cerceveler
    (frame) uzerinden anlatildigini gosterir.
    """
    hours = _parse_window_hours(window)
    since = datetime.utcnow() - timedelta(hours=hours)

    rows = await pulse_repo.top_topics_since(
        since=since, limit=limit, min_mentions=2
    )
    topic_names = [r["topic"] for r in rows]
    breakdown = await pulse_repo.frame_breakdown_for_topics(
        since=since, topics=topic_names
    )

    agg: dict = defaultdict(list)
    for r in breakdown:
        agg[r["topic"]].append((r["frame"], int(r["cnt"])))

    items = []
    for r in rows:
        topic = r["topic"]
        frames = sorted(agg.get(topic, []), key=lambda x: x[1], reverse=True)
        total = sum(c for _, c in frames)
        if total == 0:
            continue
        frame_list = [
            {"frame": f, "count": c, "pct": round(100 * c / total)}
            for f, c in frames[:5]
        ]
        items.append(
            {
                "topic": topic,
                "total": total,
                "frame_count": len(frames),
                "frames": frame_list,
            }
        )

    return {
        "window": window,
        "hours": hours,
        "generated_at": datetime.now(_IST).isoformat(),
        "count": len(items),
        "topics": items,
    }
