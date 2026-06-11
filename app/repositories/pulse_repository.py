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
