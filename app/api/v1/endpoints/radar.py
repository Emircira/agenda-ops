"""Radar modulu API uclari — etkilesim liderlik tablosu, manuel yeniden hesap ve metrik backfill."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import pytz
from fastapi import APIRouter, Depends, Query

from app.repositories.deps import get_radar_repository
from app.repositories.radar_repository import RadarRepository
from app.workers.radar_tasks import compute_radar_daily, snapshot_content_metrics

router = APIRouter()


def _parse_window_days(window: str) -> int:
    w = (window or "").strip().lower()
    if w.endswith("d"):
        w = w[:-1]
    try:
        days = int(w)
    except ValueError:
        days = 7
    return max(1, min(days, 365))


@router.get("")
async def get_radar(
    window: str = Query("7d", description="Zaman penceresi, orn: 7d, 30d"),
    platform: Optional[str] = Query(None, description="Platform filtresi (orn: x, youtube)"),
    limit: int = Query(50, ge=1, le=200),
    order: str = Query("score", description="score | engagement | reach | comments | posts"),
    radar_repo: RadarRepository = Depends(get_radar_repository),
):
    """Secilen pencere boyunca platform/kullanici bazli Radar liderlik tablosu."""
    days = _parse_window_days(window)
    tz = pytz.timezone("Europe/Istanbul")
    today = datetime.now(tz).date()
    start_day = today - timedelta(days=days - 1)

    rows = await radar_repo.leaderboard(
        start_day=start_day,
        platform=platform,
        limit=limit,
        order=order,
    )

    leaderboard = []
    for i, r in enumerate(rows, 1):
        last_active = r.get("last_active_day")
        leaderboard.append(
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
                "last_active_day": str(last_active) if last_active else None,
            }
        )

    return {
        "window": f"{days}d",
        "platform": platform or "all",
        "order": order,
        "start_day": start_day.isoformat(),
        "end_day": today.isoformat(),
        "count": len(leaderboard),
        "leaderboard": leaderboard,
    }


@router.post("/recompute")
async def recompute_radar(
    target_day: Optional[str] = Query(None, description="ISO tarih (YYYY-MM-DD); bossa dun"),
):
    """Belirli bir gun (veya dun) icin Radar skorlarini yeniden hesaplamayi tetikler."""
    task = compute_radar_daily.delay(target_day=target_day)
    return {
        "status": "queued",
        "task_id": task.id,
        "target_day": target_day or "yesterday",
    }


@router.post("/snapshot-metrics")
async def snapshot_metrics(
    since_days: Optional[int] = Query(
        None, description="Son N gun ile sinirla; bossa tum eksik metrikler islenir"
    ),
):
    """raw_json.metrics -> content_metrics materyalizasyonunu tetikler (backfill).

    Radar gercek skor uretebilmek icin content_metrics tablosuna ihtiyac duyar; ingest
    etkilesimleri yalnizca raw_json'a yazdigi icin bu uc eksik metrikleri doldurur.
    """
    task = snapshot_content_metrics.delay(since_days=since_days)
    return {
        "status": "queued",
        "task_id": task.id,
        "since_days": since_days if since_days is not None else "all",
    }
