"""Gündem Nabızı repository.

X/Twitter içeriğini `content_labels` üzerinden konu bazında toplulastırır:
"bugün ne konuşuluyor" (konu + hacim) ve "insanlar ne düşünüyor"
(ortalama sentiment + stance dağılımı). LLM çağrısı yapmaz; yalnızca
mevcut etiket verisini okur.
"""

from __future__ import annotations

from datetime import datetime
from typing import List

from sqlalchemy import case, desc, func, select

from app.models.core import Content, ContentLabel
from app.repositories.base import BaseRepository

# X/Twitter icerikleri iki farkli platform etiketiyle gelebiliyor.
_X_PLATFORMS = ["x", "twitter"]


class PulseRepository(BaseRepository):
    """X/Twitter konu + duygu + tutum toplulastırması (Gundem Nabzi)."""

    async def top_topics_since(
        self,
        *,
        since: datetime,
        limit: int = 8,
        min_mentions: int = 1,
    ) -> List[dict]:
        """Pencere icindeki X/Twitter iceriklerini konuya gore toplulastırır.

        Donen her satir: topic, mention_count, avg_sentiment, support/oppose/
        neutral_count, max_crisis. mention_count'a gore azalan siralanir.
        """
        mention_count = func.count(ContentLabel.content_id)
        stmt = (
            select(
                ContentLabel.topic.label("topic"),
                mention_count.label("mention_count"),
                func.avg(ContentLabel.sentiment_score).label("avg_sentiment"),
                func.sum(
                    case((ContentLabel.stance == "support", 1), else_=0)
                ).label("support_count"),
                func.sum(
                    case((ContentLabel.stance == "oppose", 1), else_=0)
                ).label("oppose_count"),
                func.sum(
                    case((ContentLabel.stance == "neutral", 1), else_=0)
                ).label("neutral_count"),
                func.coalesce(func.max(ContentLabel.crisis_score), 0).label("max_crisis"),
            )
            .join(Content, Content.id == ContentLabel.content_id)
            .where(
                Content.platform.in_(_X_PLATFORMS),
                Content.published_at >= since,
                ContentLabel.topic.isnot(None),
                func.length(func.trim(ContentLabel.topic)) > 0,
            )
            .group_by(ContentLabel.topic)
            .having(mention_count >= min_mentions)
            .order_by(desc(mention_count))
            .limit(limit)
        )
        res = await self._session.execute(stmt)
        return [dict(r) for r in res.mappings().all()]

    async def sample_summaries_for_topics(
        self,
        *,
        since: datetime,
        topics: List[str],
    ) -> dict:
        """Her konu icin en guncel etiket ozeti + metin ornegini dondurur.

        Donus: { topic: {"summary": str, "sample_text": str} }
        """
        if not topics:
            return {}
        stmt = (
            select(
                ContentLabel.topic,
                ContentLabel.summary,
                Content.text,
                Content.published_at,
            )
            .join(Content, Content.id == ContentLabel.content_id)
            .where(
                Content.platform.in_(_X_PLATFORMS),
                Content.published_at >= since,
                ContentLabel.topic.in_(topics),
            )
            .order_by(ContentLabel.topic, desc(Content.published_at))
        )
        res = await self._session.execute(stmt)
        out: dict = {}
        for row in res.mappings().all():
            topic = row["topic"]
            if topic in out:
                continue  # ilk gorulen = en guncel (order_by sayesinde)
            out[topic] = {
                "summary": (row["summary"] or "").strip(),
                "sample_text": (row["text"] or "")[:240].strip(),
            }
        return out

    # ------------------------------------------------------------------
    # Gundem Icgoruleri (Momentum / Kutuplasma / Naratif) icin ek sorgular
    # ------------------------------------------------------------------

    async def topic_counts_between(
        self,
        *,
        start: datetime,
        end: datetime,
    ) -> List[dict]:
        """[start, end) araliginda X/Twitter konularinin mention sayisi.

        Momentum hesabi icin iki ayri pencerede (mevcut + onceki) cagrilir.
        Donen her satir: topic, mention_count. Azalan siralanir.
        """
        mention_count = func.count(ContentLabel.content_id)
        stmt = (
            select(
                ContentLabel.topic.label("topic"),
                mention_count.label("mention_count"),
            )
            .join(Content, Content.id == ContentLabel.content_id)
            .where(
                Content.platform.in_(_X_PLATFORMS),
                Content.published_at >= start,
                Content.published_at < end,
                ContentLabel.topic.isnot(None),
                func.length(func.trim(ContentLabel.topic)) > 0,
            )
            .group_by(ContentLabel.topic)
            .order_by(desc(mention_count))
        )
        res = await self._session.execute(stmt)
        return [dict(r) for r in res.mappings().all()]

    async def topic_hourly_counts(
        self,
        *,
        since: datetime,
        topics: List[str],
    ) -> List[dict]:
        """Verilen konular icin saatlik mention sayilari (sparkline icin).

        Donen her satir: topic, bucket (ISO saat), cnt.
        """
        if not topics:
            return []
        bucket = func.date_trunc("hour", Content.published_at).label("bucket")
        mention_count = func.count(ContentLabel.content_id)
        stmt = (
            select(
                ContentLabel.topic.label("topic"),
                bucket,
                mention_count.label("cnt"),
            )
            .join(Content, Content.id == ContentLabel.content_id)
            .where(
                Content.platform.in_(_X_PLATFORMS),
                Content.published_at >= since,
                ContentLabel.topic.in_(topics),
            )
            .group_by(ContentLabel.topic, bucket)
            .order_by(ContentLabel.topic, bucket)
        )
        res = await self._session.execute(stmt)
        out: List[dict] = []
        for row in res.mappings().all():
            b = row["bucket"]
            out.append(
                {
                    "topic": row["topic"],
                    "bucket": b.isoformat() if hasattr(b, "isoformat") else str(b),
                    "cnt": int(row["cnt"]),
                }
            )
        return out

    async def frame_breakdown_for_topics(
        self,
        *,
        since: datetime,
        topics: List[str],
    ) -> List[dict]:
        """Konu basina cerceve (frame) dagilimi — Naratif/Cerceve Savasi icin.

        Donen her satir: topic, frame, cnt. Konu + cnt azalan siralanir.
        """
        if not topics:
            return []
        mention_count = func.count(ContentLabel.content_id)
        stmt = (
            select(
                ContentLabel.topic.label("topic"),
                ContentLabel.frame.label("frame"),
                mention_count.label("cnt"),
            )
            .join(Content, Content.id == ContentLabel.content_id)
            .where(
                Content.platform.in_(_X_PLATFORMS),
                Content.published_at >= since,
                ContentLabel.topic.in_(topics),
                ContentLabel.frame.isnot(None),
                func.length(func.trim(ContentLabel.frame)) > 0,
            )
            .group_by(ContentLabel.topic, ContentLabel.frame)
            .order_by(ContentLabel.topic, desc(mention_count))
        )
        res = await self._session.execute(stmt)
        return [dict(r) for r in res.mappings().all()]
