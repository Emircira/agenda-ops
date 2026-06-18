"""Gündem Nabızı + Gündem İçgörüleri API uclari — X/Twitter.

LLM kullanmaz; content_labels icindeki mevcut topic / sentiment / stance /
frame / target verisini istatistiksel olarak toplulastırır.

Uclar:
- GET /agenda        : bugun ne konusuluyor + kitle duygusu/tutumu
- GET /momentum      : yukselen/dusen gundem (mevcut vs onceki pencere + sparkline)
- GET /polarization  : kutuplasma/catisma haritasi (target bazli, stance + sentiment)
- GET /frames        : naratif/cerceve savasi (konu basina frame dagilimi)
- GET /subjects      : drill-down icin top konu + ozne listesi
- GET /subject       : tek bir konu/ozne icin detayli analiz (drill-down)
- GET /subject-account : konu/ozne hakkinda one cikan orta seviye hesabin LLM analizi
"""

from __future__ import annotations

import math
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import List, Optional

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


def _hour_axis(since: datetime, now: datetime) -> List[str]:
    """since -> now arasi saatlik eksen (YYYY-MM-DDTHH prefixleri)."""
    axis: List[str] = []
    t = since.replace(minute=0, second=0, microsecond=0)
    end_axis = now.replace(minute=0, second=0, microsecond=0)
    while t <= end_axis:
        axis.append(t.isoformat()[:13])
        t += timedelta(hours=1)
    return axis


# ---------------------------------------------------------------------------
# Hesap analizi (LLM) icin basit bellek-ici TTL cache.
# Token israfini onlemek icin ayni (by, name, hours) icin sonuc 30 dk saklanir.
# Tek surecte calisir; cok-worker'da her worker kendi cache'ini tutar (kabul edilir).
# ---------------------------------------------------------------------------
_ACCOUNT_CACHE_TTL_SECONDS = 1800
_account_cache: dict = {}


def _account_cache_get(key: str):
    entry = _account_cache.get(key)
    if not entry:
        return None
    expires_at, value = entry
    if time.time() > expires_at:
        _account_cache.pop(key, None)
        return None
    return value


def _account_cache_set(key: str, value: dict) -> None:
    _account_cache[key] = (time.time() + _ACCOUNT_CACHE_TTL_SECONDS, value)


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

    axis = _hour_axis(cur_start, now)

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
    """Kutuplasma/catisma haritasi — OZNE (target) bazli.

    Her ozne icin once tutum (stance: destek/karsi) yarilmasina bakilir; tutum
    sinyali zayifsa duygu (sentiment) kutuplasmasina (olumlu<->olumsuz) duser.
    Boylece 'Rahmi Koç', 'Kürtler' gibi spesifik gerilimler yakalanir; coarse
    'iç/dış politika' konulari yerine somut ozneler one cikar.
    """
    hours = _parse_window_hours(window)
    since = datetime.utcnow() - timedelta(hours=hours)

    rows = await pulse_repo.polarization_targets_since(
        since=since, min_mentions=4, limit=max(limit * 4, 24)
    )

    items = []
    for r in rows:
        total = int(r.get("mention_count") or 0)
        if total <= 0:
            continue
        sup = int(r.get("support_count") or 0)
        opp = int(r.get("oppose_count") or 0)
        neu = int(r.get("neutral_count") or 0)
        pos = int(r.get("pos_count") or 0)
        neg = int(r.get("neg_count") or 0)
        avg_raw = r.get("avg_sentiment")
        avg_sent = float(avg_raw) if avg_raw is not None else None

        stance_op = sup + opp
        # 1) Once tutum yarilmasi (her iki taraf da temsil ediliyorsa)
        if sup > 0 and opp > 0 and stance_op >= 2:
            basis = "stance"
            left, right, denom = sup, opp, stance_op
            left_label, right_label = "Destek", "Karşı"
            neutral_n = neu
        # 2) Tutum zayifsa duygu yarilmasina dus
        elif pos > 0 and neg > 0:
            basis = "sentiment"
            left, right, denom = pos, neg, (pos + neg)
            left_label, right_label = "Olumlu", "Olumsuz"
            neutral_n = max(0, total - pos - neg)
        else:
            continue

        if denom <= 0:
            continue
        balance = 1 - abs(left - right) / denom  # 1.0 = tam ortadan bolunmus
        polarization = round(100 * balance * (denom / total))
        left_pct = round(100 * left / denom)
        right_pct = 100 - left_pct
        items.append(
            {
                "subject": r["target"],
                "mention_count": total,
                "basis": basis,
                "left_label": left_label,
                "right_label": right_label,
                "left_pct": left_pct,
                "right_pct": right_pct,
                "neutral_count": neutral_n,
                "polarization": polarization,
                "avg_sentiment": round(avg_sent, 3) if avg_sent is not None else None,
                "crisis_score": int(r.get("max_crisis") or 0),
            }
        )

    items.sort(key=lambda x: (x["polarization"], x["mention_count"]), reverse=True)
    items = items[:limit]

    return {
        "window": window,
        "hours": hours,
        "generated_at": datetime.now(_IST).isoformat(),
        "basis": "target",
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


@router.get("/subjects")
async def get_subjects(
    window: str = Query("24h", description="Zaman penceresi: 24h, 48h, 7d"),
    limit: int = Query(12, ge=1, le=30),
    pulse_repo: PulseRepository = Depends(get_pulse_repository),
):
    """Drill-down sol panel: en cok konusulan konular (topic) ve ozneler (target)."""
    hours = _parse_window_hours(window)
    since = datetime.utcnow() - timedelta(hours=hours)

    topics = await pulse_repo.top_topics_since(since=since, limit=limit, min_mentions=2)
    targets = await pulse_repo.top_targets_since(since=since, limit=limit, min_mentions=2)

    return {
        "window": window,
        "hours": hours,
        "generated_at": datetime.now(_IST).isoformat(),
        "topics": [
            {"name": t["topic"], "count": int(t.get("mention_count") or 0)}
            for t in topics
        ],
        "targets": [
            {"name": t["name"], "count": int(t.get("mention_count") or 0)}
            for t in targets
        ],
    }


@router.get("/subject")
async def get_subject_detail(
    name: str = Query(..., description="Konu (topic) veya ozne (target) adi"),
    by: str = Query("topic", description="'topic' veya 'target'"),
    window: str = Query("24h", description="Zaman penceresi: 24h, 48h, 7d"),
    pulse_repo: PulseRepository = Depends(get_pulse_repository),
):
    """Tek bir konu/ozne icin detayli analiz (drill-down detay paneli).

    Tutum dagilimi, duygu dagilimi, naratif (frame), ornek gonderiler ve
    saatlik hacim (spark) dondurur. Ayrica en cok etkilesim alan tek gonderiyi
    (top_post) dondurur.
    """
    by = "target" if by == "target" else "topic"
    hours = _parse_window_hours(window)
    now = datetime.utcnow()
    since = now - timedelta(hours=hours)

    agg = await pulse_repo.subject_aggregate(since=since, name=name, by=by)
    total = int(agg.get("mention_count") or 0) if agg else 0
    if not agg or total == 0:
        return {
            "found": False,
            "subject": name,
            "by": by,
            "window": window,
            "generated_at": datetime.now(_IST).isoformat(),
        }

    sup = int(agg.get("support_count") or 0)
    opp = int(agg.get("oppose_count") or 0)
    neu = int(agg.get("neutral_count") or 0)
    stance_total = (sup + opp + neu) or 1
    pos = int(agg.get("pos_count") or 0)
    neg = int(agg.get("neg_count") or 0)
    neu_sent = max(0, total - pos - neg)
    avg_raw = agg.get("avg_sentiment")
    avg_sent = float(avg_raw) if avg_raw is not None else None

    frames_rows = await pulse_repo.subject_frame_breakdown(since=since, name=name, by=by)
    frame_total = sum(int(r["cnt"]) for r in frames_rows) or 1
    frames = [
        {
            "frame": r["frame"],
            "count": int(r["cnt"]),
            "pct": round(100 * int(r["cnt"]) / frame_total),
        }
        for r in frames_rows[:6]
    ]

    samples = await pulse_repo.subject_samples(since=since, name=name, by=by, limit=8)

    top_post = await pulse_repo.subject_top_post(since=since, name=name, by=by)

    hourly = await pulse_repo.subject_hourly(since=since, name=name, by=by)
    hmap: dict = defaultdict(int)
    for row in hourly:
        hmap[str(row["bucket"])[:13]] += int(row["cnt"])
    axis = _hour_axis(since, now)
    spark = [hmap.get(p, 0) for p in axis]

    return {
        "found": True,
        "subject": name,
        "by": by,
        "window": window,
        "hours": hours,
        "generated_at": datetime.now(_IST).isoformat(),
        "mention_count": total,
        "avg_sentiment": round(avg_sent, 3) if avg_sent is not None else None,
        "mood": _mood_label(avg_sent),
        "crisis_score": int(agg.get("max_crisis") or 0),
        "manipulation_pct": round(100 * float(agg.get("max_manip") or 0.0)),
        "bot_pct": round(100 * float(agg.get("max_bot") or 0.0)),
        "stance": {
            "support_pct": round(100 * sup / stance_total),
            "oppose_pct": round(100 * opp / stance_total),
            "neutral_pct": round(100 * neu / stance_total),
        },
        "sentiment": {
            "pos_pct": round(100 * pos / total),
            "neg_pct": round(100 * neg / total),
            "neu_pct": round(100 * neu_sent / total),
        },
        "frames": frames,
        "samples": samples,
        "top_post": top_post,
        "spark": spark,
    }


@router.get("/subject-account")
async def get_subject_account_analysis(
    name: str = Query(..., description="Konu (topic) veya ozne (target) adi"),
    by: str = Query("topic", description="'topic' veya 'target'"),
    window: str = Query("24h", description="Zaman penceresi: 24h, 48h, 7d"),
    pulse_repo: PulseRepository = Depends(get_pulse_repository),
):
    """Konu/ozne hakkinda konusan 'orta seviye (dev olmayan)' bir hesabi secip
    Gemini ile analiz eder (durus profili + etki/manipulasyon/bot degerlendirmesi).

    Token israfini onlemek icin sonuc 30 dk bellek-ici cache'te tutulur.
    Uygun hesap bulunamazsa found=False doner (bu da cache'lenir).
    """
    by = "target" if by == "target" else "topic"
    hours = _parse_window_hours(window)
    now = datetime.utcnow()
    since = now - timedelta(hours=hours)

    cache_key = f"{by}:{name}:{hours}"
    cached = _account_cache_get(cache_key)
    if cached is not None:
        return {**cached, "cached": True}

    account = await pulse_repo.subject_top_account(since=since, name=name, by=by)
    if not account:
        payload = {
            "found": False,
            "subject": name,
            "by": by,
            "window": window,
            "hours": hours,
            "generated_at": datetime.now(_IST).isoformat(),
        }
        _account_cache_set(cache_key, payload)
        return payload

    posts = account.get("posts") or []

    # Lazy import: LLM servisi yalnizca bu uc cagrildiginda yuklenir.
    from app.services.account_analysis_service import analyze_account_profile

    analysis = await analyze_account_profile(
        account_name=account["author"],
        subject=name,
        by=by,
        posts=posts,
    )

    payload = {
        "found": True,
        "subject": name,
        "by": by,
        "window": window,
        "hours": hours,
        "generated_at": datetime.now(_IST).isoformat(),
        "account": {
            "author": account["author"],
            "engagement": account["engagement"],
            "post_count": account["post_count"],
            "median_engagement": account.get("median_engagement"),
            "candidate_count": account.get("candidate_count"),
        },
        "samples": posts[:5],
        "analysis": analysis,
    }
    _account_cache_set(cache_key, payload)
    return payload
