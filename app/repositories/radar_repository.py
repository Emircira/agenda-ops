"""Radar modülü repository.

`contents` + `content_metrics` tablolarini gun bazinda toplulastirir, Radar skorunu
hesaplar ve `radar_user_daily` tablosuna yazar/sorgular.

Skor tasarimi:
    base  = w_comment * comments_received
          + w_engage  * total_engagement
          + w_reach   * log1p(reach_estimate)
    score = base * (1 + streak_bonus * (consecutive_days - 1))
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import delete, select, text

from app.models.radar import RadarUserDaily
from app.repositories.base import BaseRepository

# Skor agirliklari (Radar tasarimi)
W_COMMENT = 3.0
W_ENGAGE = 1.0
W_REACH = 2.0
STREAK_BONUS = 0.15

# Liderlik tablosu siralama whitelisti (SQL injection korumasi)
_ORDER_COLUMNS = {
    "score": "total_score",
    "engagement": "total_engagement",
    "reach": "total_reach",
    "comments": "total_comments",
    "posts": "total_posts",
}


class RadarRepository(BaseRepository):
    """contents + content_metrics -> radar_user_daily toplulastirma ve sorgu."""

    async def snapshot_metrics_from_raw_json(
        self,
        *,
        since: Optional[datetime] = None,
        commit: bool = False,
    ) -> int:
        """`contents.raw_json.metrics` icindeki etkilesimleri `content_metrics`'e materyalize eder.

        Ingest sirasinda Twitter etkilesimleri yalnizca raw_json icine yazildigi icin,
        Radar'in besledigi `content_metrics` tablosu bos kaliyordu. Bu metot eksik satirlari
        (NOT EXISTS) doldurur; idempotenttir (tekrar calistirmak guvenli).

        raw_json.metrics semasi: {"likes": int, "retweets": int, "replies": int}
        Esleme: likes->likes, replies->replies, retweets->reposts, views=0 (kaynakta yok).

        :param since: verilirse yalnizca bu tarihten sonra yayinlanan icerikler islenir.
        :returns: eklenen content_metrics satir sayisi.
        """
        where_since = "AND c.published_at >= :since" if since else ""
        sql = text(
            f"""
            INSERT INTO content_metrics (content_id, captured_at, likes, replies, reposts, views)
            SELECT
                c.id,
                COALESCE(c.fetched_at, c.published_at, now()),
                COALESCE(CASE WHEN (c.raw_json->'metrics'->>'likes') ~ '^[0-9]+$'
                         THEN (c.raw_json->'metrics'->>'likes')::int ELSE 0 END, 0),
                COALESCE(CASE WHEN (c.raw_json->'metrics'->>'replies') ~ '^[0-9]+$'
                         THEN (c.raw_json->'metrics'->>'replies')::int ELSE 0 END, 0),
                COALESCE(CASE WHEN (c.raw_json->'metrics'->>'retweets') ~ '^[0-9]+$'
                         THEN (c.raw_json->'metrics'->>'retweets')::int ELSE 0 END, 0),
                0
            FROM contents c
            WHERE c.raw_json -> 'metrics' IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM content_metrics m WHERE m.content_id = c.id
              )
              {where_since}
            """
        )
        params: dict = {}
        if since:
            params["since"] = since
        res = await self._session.execute(sql, params)
        if commit:
            await self.commit()
        return res.rowcount or 0

    async def aggregate_activity_for_day(self, day: date) -> list[dict]:
        """Verilen gun icin platform/kullanici bazli ham etkilesim toplamlari.

        Her icerik icin en guncel metrik anlik goruntusu (captured_at DESC) alinir.
        """
        start = datetime(day.year, day.month, day.day)
        end = start + timedelta(days=1)

        sql = text(
            """
            WITH latest_metric AS (
                SELECT DISTINCT ON (cm.content_id)
                    cm.content_id,
                    cm.likes,
                    cm.replies,
                    cm.reposts,
                    cm.views
                FROM content_metrics cm
                ORDER BY cm.content_id, cm.captured_at DESC
            )
            SELECT
                c.platform AS platform,
                c.author_name AS username,
                COUNT(*) AS posts_count,
                COALESCE(SUM(COALESCE(lm.replies, 0)), 0) AS comments_received,
                COALESCE(SUM(
                    COALESCE(lm.likes, 0) + COALESCE(lm.replies, 0) + COALESCE(lm.reposts, 0)
                ), 0) AS total_engagement,
                COALESCE(SUM(COALESCE(lm.views, 0)), 0) AS reach_estimate
            FROM contents c
            LEFT JOIN latest_metric lm ON lm.content_id = c.id
            WHERE c.published_at >= :start AND c.published_at < :end
              AND c.author_name IS NOT NULL
              AND c.author_name <> ''
            GROUP BY c.platform, c.author_name
            """
        )
        res = await self._session.execute(sql, {"start": start, "end": end})
        return [dict(r) for r in res.mappings().all()]

    async def get_consecutive_days(self, platform: str, username: str, prev_day: date) -> int:
        """prev_day (gun-1) kaydindaki streak; yoksa 0 doner."""
        stmt = select(RadarUserDaily.consecutive_days).where(
            RadarUserDaily.platform == platform,
            RadarUserDaily.username == username,
            RadarUserDaily.day == prev_day,
        )
        res = await self._session.execute(stmt)
        val = res.scalar_one_or_none()
        return int(val) if val is not None else 0

    async def upsert_daily(
        self,
        *,
        platform: str,
        username: str,
        day: date,
        posts_count: int,
        comments_received: int,
        total_engagement: float,
        reach_estimate: float,
        consecutive_days: int,
        radar_score: float,
        commit: bool = False,
    ) -> RadarUserDaily:
        """(platform, username, day) anahtarinda upsert."""
        stmt = select(RadarUserDaily).where(
            RadarUserDaily.platform == platform,
            RadarUserDaily.username == username,
            RadarUserDaily.day == day,
        )
        res = await self._session.execute(stmt)
        row = res.scalar_one_or_none()
        if row is None:
            row = RadarUserDaily(platform=platform, username=username, day=day)
            self._session.add(row)
        row.posts_count = posts_count
        row.comments_received = comments_received
        row.total_engagement = total_engagement
        row.reach_estimate = reach_estimate
        row.consecutive_days = consecutive_days
        row.radar_score = radar_score
        row.computed_at = datetime.utcnow()
        if commit:
            await self.commit()
        return row

    @staticmethod
    def compute_score(
        *,
        comments_received: float,
        total_engagement: float,
        reach_estimate: float,
        consecutive_days: int,
    ) -> float:
        """Radar skoru: etkilesim + erisim + streak bonusu."""
        base = (
            W_COMMENT * float(comments_received)
            + W_ENGAGE * float(total_engagement)
            + W_REACH * math.log1p(max(0.0, float(reach_estimate)))
        )
        streak_multiplier = 1.0 + STREAK_BONUS * max(0, int(consecutive_days) - 1)
        return round(base * streak_multiplier, 4)

    async def leaderboard(
        self,
        *,
        start_day: date,
        platform: Optional[str],
        limit: int,
        order: str,
    ) -> list[dict]:
        """Pencere boyunca platform/kullanici bazli toplulastirilmis liderlik tablosu."""
        order_col = _ORDER_COLUMNS.get(order, "total_score")
        platform_filter = "AND platform = :platform" if platform else ""
        sql = text(
            f"""
            SELECT
                platform,
                username,
                SUM(posts_count) AS total_posts,
                SUM(comments_received) AS total_comments,
                SUM(total_engagement) AS total_engagement,
                SUM(reach_estimate) AS total_reach,
                SUM(radar_score) AS total_score,
                MAX(consecutive_days) AS max_streak,
                MAX(day) AS last_active_day
            FROM radar_user_daily
            WHERE day >= :start_day
            {platform_filter}
            GROUP BY platform, username
            ORDER BY {order_col} DESC
            LIMIT :limit
            """
        )
        params: dict = {"start_day": start_day, "limit": limit}
        if platform:
            params["platform"] = platform
        res = await self._session.execute(sql, params)
        return [dict(r) for r in res.mappings().all()]

    async def delete_before(self, cutoff_day: date, commit: bool = False) -> int:
        """Retention: cutoff_day oncesi satirlari siler."""
        stmt = delete(RadarUserDaily).where(RadarUserDaily.day < cutoff_day)
        res = await self._session.execute(stmt)
        if commit:
            await self.commit()
        return res.rowcount or 0
