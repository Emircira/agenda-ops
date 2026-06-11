"""Radar modulu Celery gorevleri.

`compute_radar_daily`: varsayilan olarak (Europe/Istanbul) bir onceki gunun
iceriklerini toplulastirip `radar_user_daily` tablosuna yazar; streak (ust uste
aktif gun) bonusu uygular. Ardindan gunun en yuksek skorlu hesaplari icin
SystemAlert (yukselen hesap uyarisi) uretir.

`snapshot_content_metrics`: `contents.raw_json.metrics` icindeki etkilesimleri
`content_metrics` tablosuna materyalize eder (backfill + sureklilik). Radar bu
tablodan beslendigi icin, ingest sadece raw_json'a yazdiginda skorlar 0 kalmasin diye.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import Optional

import pytz
from loguru import logger

from app.core.celery_app import celery_app
from app.db.session import AsyncSessionLocal
from app.repositories.alert_repository import AlertRepository
from app.repositories.radar_repository import RadarRepository

# Radar -> alarm uretimi esikleri
RADAR_ALERT_TOP_N = 3
RADAR_ALERT_MIN_SCORE = 50.0
RADAR_ALERT_HIGH_SCORE = 200.0


def _run_async(coro):
    return asyncio.run(coro)


def _resolve_target_day(target_day: Optional[str]) -> date:
    if target_day:
        return date.fromisoformat(target_day)
    tz = pytz.timezone("Europe/Istanbul")
    now_ist = datetime.now(tz)
    return (now_ist - timedelta(days=1)).date()


async def _emit_top_mover_alerts(session, day: date) -> int:
    """Gunun en yuksek Radar skorlu hesaplari icin SystemAlert uretir (idempotent).

    Ayni gun+hesap icin tekrar calistirildiginda yeni uyari uretmez
    (exists_by_type_message ile mesaj bazli dedup).
    """
    radar_repo = RadarRepository(session)
    alert_repo = AlertRepository(session)
    movers = await radar_repo.leaderboard(
        start_day=day, platform=None, limit=RADAR_ALERT_TOP_N, order="score"
    )
    created = 0
    for r in movers:
        score = float(r.get("total_score") or 0)
        if score < RADAR_ALERT_MIN_SCORE:
            continue
        username = r.get("username") or "?"
        platform = r.get("platform") or "?"
        posts = int(r.get("total_posts") or 0)
        engagement = int(r.get("total_engagement") or 0)
        streak = int(r.get("max_streak") or 0)
        message = (
            f"\U0001F4E1 Radar yukselen: {username} ({platform}) - {day.isoformat()} gunu "
            f"{score:.0f} puanla one cikti ({posts} gonderi, {engagement} etkilesim, "
            f"seri {streak}g)."
        )
        if await alert_repo.exists_by_type_message(
            alert_type="radar_top_mover", message=message
        ):
            continue
        severity = "high" if score >= RADAR_ALERT_HIGH_SCORE else "medium"
        await alert_repo.create_alert(
            alert_type="radar_top_mover",
            severity=severity,
            message=message,
            commit=False,
        )
        created += 1
    if created:
        await alert_repo.commit()
    return created


@celery_app.task(name="snapshot_content_metrics", bind=True, max_retries=2)
def snapshot_content_metrics(self, since_days: Optional[int] = None):
    """raw_json.metrics -> content_metrics materyalizasyonu (eksik satirlari doldurur).

    :param since_days: verilirse son N gunluk icerikle sinirlar; bossa tum eksikler islenir.
    """

    async def _job():
        since: Optional[datetime] = None
        if since_days is not None:
            since = datetime.utcnow() - timedelta(days=int(since_days))

        async with AsyncSessionLocal() as session:
            repo = RadarRepository(session)
            try:
                written = await repo.snapshot_metrics_from_raw_json(since=since, commit=True)
            except Exception as e:
                await repo.rollback()
                logger.debug(f"snapshot_content_metrics hata: {e}")
                return f"metrics:error:{e}"

        logger.info(f"\U0001F4E1 Radar metrik snapshot: {written} satir content_metrics'e yazildi.")
        return f"metrics:ok:{written}"

    try:
        return _run_async(_job())
    except Exception as e:
        logger.debug(f"snapshot_content_metrics: {e}")
        return f"metrics:error:{e}"


@celery_app.task(name="compute_radar_daily", bind=True, max_retries=2)
def compute_radar_daily(self, target_day: Optional[str] = None):
    """Bir gunun (varsayilan: dun) Radar skorlarini hesaplar, yazar ve uyari uretir."""

    async def _job():
        day = _resolve_target_day(target_day)
        prev_day = day - timedelta(days=1)

        async with AsyncSessionLocal() as session:
            repo = RadarRepository(session)
            try:
                aggregates = await repo.aggregate_activity_for_day(day)
            except Exception as e:
                logger.debug(f"Radar toplulastirma okunamadi ({day}): {e}")
                return f"radar:db_error:{day.isoformat()}"

            if not aggregates:
                logger.info(f"\U0001F4E1 Radar: {day.isoformat()} icin icerik yok.")
                return f"radar:no_data:{day.isoformat()}"

            written = 0
            for row in aggregates:
                platform = row.get("platform")
                username = row.get("username")
                if not platform or not username:
                    continue

                comments_received = int(row.get("comments_received") or 0)
                total_engagement = float(row.get("total_engagement") or 0)
                reach_estimate = float(row.get("reach_estimate") or 0)
                posts_count = int(row.get("posts_count") or 0)

                prev_streak = await repo.get_consecutive_days(platform, username, prev_day)
                consecutive_days = prev_streak + 1 if prev_streak > 0 else 1

                score = RadarRepository.compute_score(
                    comments_received=comments_received,
                    total_engagement=total_engagement,
                    reach_estimate=reach_estimate,
                    consecutive_days=consecutive_days,
                )

                await repo.upsert_daily(
                    platform=platform,
                    username=username,
                    day=day,
                    posts_count=posts_count,
                    comments_received=comments_received,
                    total_engagement=total_engagement,
                    reach_estimate=reach_estimate,
                    consecutive_days=consecutive_days,
                    radar_score=score,
                    commit=False,
                )
                written += 1

            try:
                await repo.commit()
            except Exception as e:
                await repo.rollback()
                logger.debug(f"Radar commit hatasi ({day}): {e}")
                return f"radar:commit_failed:{day.isoformat()}"

            # Radar -> alarm: gunun en yukselen hesaplari icin bildirim uret (idempotent).
            try:
                alerts_created = await _emit_top_mover_alerts(session, day)
            except Exception as e:
                logger.debug(f"Radar alarm uretimi atlandi ({day}): {e}")
                alerts_created = 0

        logger.info(
            f"\U0001F4E1 Radar: {day.isoformat()} icin {written} kullanici skoru, "
            f"{alerts_created} yukselen-hesap uyarisi yazildi."
        )
        return f"radar:ok:{day.isoformat()}:{written}"

    try:
        return _run_async(_job())
    except Exception as e:
        logger.debug(f"compute_radar_daily: {e}")
        return f"radar:error:{e}"
