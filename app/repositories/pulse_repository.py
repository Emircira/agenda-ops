"""Gundem Nabzi + Gundem Icgoruleri repository.

X/Twitter icerigini content_labels uzerinden topluluk/istatistik bazinda okur;
LLM cagrisi yapmaz. Konu (topic), ozne (target), cerceve (frame), tutum (stance)
ve duygu (sentiment) alanlarini toplulastirir.
"""

from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import List, Optional

from sqlalchemy import case, desc, func, select

from app.data.account_blocklist import is_blocklisted
from app.models.core import Content, ContentLabel, ContentMetric
from app.repositories.base import BaseRepository

# X/Twitter icerikleri iki farkli platform etiketiyle gelebiliyor.
_X_PLATFORMS = ["x", "twitter"]

# Stance token normalizasyonu: model EN/TR + buyuk-kucuk harf karisik dondurebilir.
_SUPPORT_TOKENS = ["support", "destek", "lehte", "pro", "for", "olumlu", "positive", "taraftar", "yandas"]
_OPPOSE_TOKENS = ["oppose", "karsi", "kar\u015f\u0131", "aleyhte", "against", "anti", "olumsuz", "negative", "muhalif"]
_NEUTRAL_TOKENS = ["neutral", "notr", "n\u00f6tr", "tarafsiz", "taraf\u015f\u0131z", "taraf\u00b1z", "mixed", "karisik", "kar\u0131\u015f\u0131k", "belirsiz"]

# Anlamsiz/varsayilan target degerleri (kutuplasma icin gurultu).
_JUNK_TARGETS = ["", "bilinmiyor", "genel", "yok", "none", "n/a", "na", "-", "belirsiz", "diger", "di\u011fer"]


def _stance_norm():
    """lower(trim(coalesce(stance,''))) ifadesi."""
    return func.lower(func.trim(func.coalesce(ContentLabel.stance, "")))


class PulseRepository(BaseRepository):
    """X/Twitter konu + duygu + tutum + ozne toplulastirmasi."""

    def _subject_col(self, by: str):
        """by='target' ise ContentLabel.target, aksi halde ContentLabel.topic."""
        return ContentLabel.target if by == "target" else ContentLabel.topic

    async def top_topics_since(
        self,
        *,
        since: datetime,
        limit: int = 8,
        min_mentions: int = 1,
    ) -> List[dict]:
        """Pencere icindeki X/Twitter iceriklerini konuya gore toplulastirir."""
        mention_count = func.count(ContentLabel.content_id)
        sn = _stance_norm()
        stmt = (
            select(
                ContentLabel.topic.label("topic"),
                mention_count.label("mention_count"),
                func.avg(ContentLabel.sentiment_score).label("avg_sentiment"),
                func.sum(case((sn.in_(_SUPPORT_TOKENS), 1), else_=0)).label("support_count"),
                func.sum(case((sn.in_(_OPPOSE_TOKENS), 1), else_=0)).label("oppose_count"),
                func.sum(case((sn.in_(_NEUTRAL_TOKENS), 1), else_=0)).label("neutral_count"),
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
        """Her konu icin en guncel etiket ozeti + metin ornegi."""
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
                continue
            out[topic] = {
                "summary": (row["summary"] or "").strip(),
                "sample_text": (row["text"] or "")[:240].strip(),
            }
        return out

    # ------------------------------------------------------------------
    # Momentum (Yukselen Gundem)
    # ------------------------------------------------------------------

    async def topic_counts_between(self, *, start: datetime, end: datetime) -> List[dict]:
        """[start, end) araliginda X/Twitter konularinin mention sayisi."""
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

    async def topic_hourly_counts(self, *, since: datetime, topics: List[str]) -> List[dict]:
        """Verilen konular icin saatlik mention sayilari (sparkline)."""
        if not topics:
            return []
        bucket = func.date_trunc("hour", Content.published_at).label("bucket")
        mention_count = func.count(ContentLabel.content_id)
        stmt = (
            select(ContentLabel.topic.label("topic"), bucket, mention_count.label("cnt"))
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
            out.append({
                "topic": row["topic"],
                "bucket": b.isoformat() if hasattr(b, "isoformat") else str(b),
                "cnt": int(row["cnt"]),
            })
        return out

    async def frame_breakdown_for_topics(self, *, since: datetime, topics: List[str]) -> List[dict]:
        """Konu basina cerceve (frame) dagilimi (Naratif)."""
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

    # ------------------------------------------------------------------
    # Kutuplasma (target bazli, stance + sentiment)
    # ------------------------------------------------------------------

    async def polarization_targets_since(
        self,
        *,
        since: datetime,
        min_mentions: int = 4,
        limit: int = 40,
    ) -> List[dict]:
        """Ozne (target) bazinda tutum + duygu yarilmasi.

        Donen her satir: target, mention_count, support/oppose/neutral_count
        (normalize stance), pos/neg_count (sentiment esigi), avg_sentiment,
        max_crisis. Anlamsiz target'lar elenir.
        """
        sn = _stance_norm()
        target_norm = func.lower(func.trim(func.coalesce(ContentLabel.target, "")))
        mention_count = func.count(ContentLabel.content_id)
        stmt = (
            select(
                ContentLabel.target.label("target"),
                mention_count.label("mention_count"),
                func.sum(case((sn.in_(_SUPPORT_TOKENS), 1), else_=0)).label("support_count"),
                func.sum(case((sn.in_(_OPPOSE_TOKENS), 1), else_=0)).label("oppose_count"),
                func.sum(case((sn.in_(_NEUTRAL_TOKENS), 1), else_=0)).label("neutral_count"),
                func.sum(case((ContentLabel.sentiment_score >= 0.25, 1), else_=0)).label("pos_count"),
                func.sum(case((ContentLabel.sentiment_score <= -0.25, 1), else_=0)).label("neg_count"),
                func.avg(ContentLabel.sentiment_score).label("avg_sentiment"),
                func.coalesce(func.max(ContentLabel.crisis_score), 0).label("max_crisis"),
            )
            .join(Content, Content.id == ContentLabel.content_id)
            .where(
                Content.platform.in_(_X_PLATFORMS),
                Content.published_at >= since,
                ContentLabel.target.isnot(None),
                func.length(func.trim(ContentLabel.target)) > 0,
                target_norm.notin_(_JUNK_TARGETS),
            )
            .group_by(ContentLabel.target)
            .having(mention_count >= min_mentions)
            .order_by(desc(mention_count))
            .limit(limit)
        )
        res = await self._session.execute(stmt)
        return [dict(r) for r in res.mappings().all()]

    async def top_targets_since(
        self,
        *,
        since: datetime,
        limit: int = 12,
        min_mentions: int = 2,
    ) -> List[dict]:
        """En cok konusulan ozneler (target) listesi (drill-down sol panel)."""
        target_norm = func.lower(func.trim(func.coalesce(ContentLabel.target, "")))
        mention_count = func.count(ContentLabel.content_id)
        stmt = (
            select(
                ContentLabel.target.label("name"),
                mention_count.label("mention_count"),
                func.avg(ContentLabel.sentiment_score).label("avg_sentiment"),
            )
            .join(Content, Content.id == ContentLabel.content_id)
            .where(
                Content.platform.in_(_X_PLATFORMS),
                Content.published_at >= since,
                ContentLabel.target.isnot(None),
                func.length(func.trim(ContentLabel.target)) > 0,
                target_norm.notin_(_JUNK_TARGETS),
            )
            .group_by(ContentLabel.target)
            .having(mention_count >= min_mentions)
            .order_by(desc(mention_count))
            .limit(limit)
        )
        res = await self._session.execute(stmt)
        return [dict(r) for r in res.mappings().all()]

    # ------------------------------------------------------------------
    # Konu Analizi (drill-down detay)
    # ------------------------------------------------------------------

    async def subject_aggregate(self, *, since: datetime, name: str, by: str) -> Optional[dict]:
        """Tek bir konu/ozne icin toplam istatistik."""
        col = self._subject_col(by)
        sn = _stance_norm()
        mention_count = func.count(ContentLabel.content_id)
        stmt = (
            select(
                mention_count.label("mention_count"),
                func.avg(ContentLabel.sentiment_score).label("avg_sentiment"),
                func.sum(case((sn.in_(_SUPPORT_TOKENS), 1), else_=0)).label("support_count"),
                func.sum(case((sn.in_(_OPPOSE_TOKENS), 1), else_=0)).label("oppose_count"),
                func.sum(case((sn.in_(_NEUTRAL_TOKENS), 1), else_=0)).label("neutral_count"),
                func.sum(case((ContentLabel.sentiment_score >= 0.25, 1), else_=0)).label("pos_count"),
                func.sum(case((ContentLabel.sentiment_score <= -0.25, 1), else_=0)).label("neg_count"),
                func.coalesce(func.max(ContentLabel.crisis_score), 0).label("max_crisis"),
                func.coalesce(func.max(ContentLabel.manipulation_prob), 0.0).label("max_manip"),
                func.coalesce(func.max(ContentLabel.bot_likelihood), 0.0).label("max_bot"),
            )
            .join(Content, Content.id == ContentLabel.content_id)
            .where(
                Content.platform.in_(_X_PLATFORMS),
                Content.published_at >= since,
                col == name,
            )
        )
        res = await self._session.execute(stmt)
        row = res.mappings().first()
        return dict(row) if row else None

    async def subject_frame_breakdown(self, *, since: datetime, name: str, by: str) -> List[dict]:
        """Tek konu/ozne icin frame dagilimi."""
        col = self._subject_col(by)
        mention_count = func.count(ContentLabel.content_id)
        stmt = (
            select(ContentLabel.frame.label("frame"), mention_count.label("cnt"))
            .join(Content, Content.id == ContentLabel.content_id)
            .where(
                Content.platform.in_(_X_PLATFORMS),
                Content.published_at >= since,
                col == name,
                ContentLabel.frame.isnot(None),
                func.length(func.trim(ContentLabel.frame)) > 0,
            )
            .group_by(ContentLabel.frame)
            .order_by(desc(mention_count))
        )
        res = await self._session.execute(stmt)
        return [dict(r) for r in res.mappings().all()]

    async def subject_samples(self, *, since: datetime, name: str, by: str, limit: int = 8) -> List[dict]:
        """Tek konu/ozne icin ornek gonderiler."""
        col = self._subject_col(by)
        stmt = (
            select(
                Content.text,
                Content.author_name,
                Content.url,
                Content.published_at,
                ContentLabel.stance,
                ContentLabel.sentiment_score,
                ContentLabel.summary,
            )
            .join(Content, Content.id == ContentLabel.content_id)
            .where(
                Content.platform.in_(_X_PLATFORMS),
                Content.published_at >= since,
                col == name,
            )
            .order_by(desc(Content.published_at))
            .limit(limit)
        )
        res = await self._session.execute(stmt)
        out: List[dict] = []
        for row in res.mappings().all():
            out.append({
                "text": (row["text"] or "")[:280].strip(),
                "author": (row["author_name"] or "").strip(),
                "url": row["url"] or "",
                "published_at": row["published_at"].isoformat() if row["published_at"] else None,
                "stance": (row["stance"] or "").strip(),
                "sentiment_score": float(row["sentiment_score"]) if row["sentiment_score"] is not None else None,
            })
        return out

    async def subject_top_post(self, *, since: datetime, name: str, by: str) -> Optional[dict]:
        """Tek konu/ozne icin EN COK ETKILESIM alan tek gonderi.

        Etkilesim = likes + replies + reposts (kullanici tercihi; views haric tutuldu).
        Bir icerigin content_metrics tablosunda birden cok snapshot satiri
        olabildigi icin her metrik MAX ile alinir (begeni zamanla arttigindan
        max ~ guncel deger). content_metrics'e JOIN gerektigi icin hic metrigi
        olmayan gonderiler bu sorguya girmez. Eslesme yoksa None doner.

        Donen alanlar: text, author, url, published_at, stance, sentiment_score,
        likes, replies, reposts, views, engagement.
        """
        col = self._subject_col(by)
        likes = func.coalesce(func.max(ContentMetric.likes), 0)
        replies = func.coalesce(func.max(ContentMetric.replies), 0)
        reposts = func.coalesce(func.max(ContentMetric.reposts), 0)
        views = func.coalesce(func.max(ContentMetric.views), 0)
        engagement = likes + replies + reposts
        stmt = (
            select(
                Content.text.label("text"),
                Content.author_name.label("author_name"),
                Content.url.label("url"),
                Content.published_at.label("published_at"),
                ContentLabel.stance.label("stance"),
                ContentLabel.sentiment_score.label("sentiment_score"),
                likes.label("likes"),
                replies.label("replies"),
                reposts.label("reposts"),
                views.label("views"),
                engagement.label("engagement"),
            )
            .join(Content, Content.id == ContentLabel.content_id)
            .join(ContentMetric, ContentMetric.content_id == Content.id)
            .where(
                Content.platform.in_(_X_PLATFORMS),
                Content.published_at >= since,
                col == name,
            )
            .group_by(
                Content.id,
                ContentLabel.stance,
                ContentLabel.sentiment_score,
            )
            .order_by(desc(engagement))
            .limit(1)
        )
        res = await self._session.execute(stmt)
        row = res.mappings().first()
        if not row:
            return None
        return {
            "text": (row["text"] or "")[:280].strip(),
            "author": (row["author_name"] or "").strip(),
            "url": row["url"] or "",
            "published_at": row["published_at"].isoformat() if row["published_at"] else None,
            "stance": (row["stance"] or "").strip(),
            "sentiment_score": float(row["sentiment_score"]) if row["sentiment_score"] is not None else None,
            "likes": int(row["likes"] or 0),
            "replies": int(row["replies"] or 0),
            "reposts": int(row["reposts"] or 0),
            "views": int(row["views"] or 0),
            "engagement": int(row["engagement"] or 0),
        }

    async def subject_account_candidates(
        self,
        *,
        since: datetime,
        name: str,
        by: str,
        max_contents: int = 600,
    ) -> List[dict]:
        """Konu/ozne hakkindaki gonderileri (icerik bazinda) etkilesimle birlikte doner.

        Her icerik icin etkilesim = max(likes) + max(replies) + max(reposts)
        (content_metrics'te birden cok snapshot olabildigi icin MAX alinir;
        views haric -- kullanici tercihi). author_name bos olanlar elenir.

        Retweet'ler (metni 'RT @' ile baslayan gonderiler) elenir: bunlar hesabin
        kendi sesi degil, baskasinin gonderisinin paylasimi oldugu icin 'one cikan
        hesap' analizine girmemeli (kullanici tercihi).
        Hesap secimi/eleme Python tarafinda yapilir (subject_top_account).
        """
        col = self._subject_col(by)
        likes = func.coalesce(func.max(ContentMetric.likes), 0)
        replies = func.coalesce(func.max(ContentMetric.replies), 0)
        reposts = func.coalesce(func.max(ContentMetric.reposts), 0)
        engagement = likes + replies + reposts
        stmt = (
            select(
                Content.author_name.label("author_name"),
                Content.text.label("text"),
                Content.url.label("url"),
                Content.published_at.label("published_at"),
                ContentLabel.stance.label("stance"),
                ContentLabel.sentiment_score.label("sentiment_score"),
                engagement.label("engagement"),
            )
            .join(Content, Content.id == ContentLabel.content_id)
            .join(ContentMetric, ContentMetric.content_id == Content.id)
            .where(
                Content.platform.in_(_X_PLATFORMS),
                Content.published_at >= since,
                col == name,
                Content.author_name.isnot(None),
                func.length(func.trim(Content.author_name)) > 0,
                func.trim(func.coalesce(Content.text, "")).notlike("RT @%"),
            )
            .group_by(
                Content.id,
                Content.author_name,
                Content.text,
                Content.url,
                Content.published_at,
                ContentLabel.stance,
                ContentLabel.sentiment_score,
            )
            .order_by(desc(engagement))
            .limit(max_contents)
        )
        res = await self._session.execute(stmt)
        return [dict(r) for r in res.mappings().all()]

    async def subject_top_account(
        self,
        *,
        since: datetime,
        name: str,
        by: str,
        median_factor: float = 5.0,
        min_posts: int = 2,
        max_posts_for_llm: int = 12,
    ) -> Optional[dict]:
        """Konu/ozne hakkinda konusan 'orta seviye (dev olmayan)' bir hesap secer.

        Adimlar:
          1) Hesap basina toplam etkilesim (begeni+yorum+RT) ve gonderi sayisi.
          2) Kara listedeki hesaplar (cumhurbaskani vb.) elenir.
          3) min_posts altindaki tek-atislik hesaplar elenir (hepsi elenirse gevsetilir).
          4) Etkilesimi medyanin median_factor (vars. 5) katindan fazla olan
             'dev' hesaplar elenir.
          5) Kalanlardan medyan konumundaki (orta seviye) hesap secilir.

        Donen alanlar: author, engagement, post_count, candidate_count,
        median_engagement ve LLM icin kisaltilmis posts listesi. Eslesme yoksa None.
        """
        rows = await self.subject_account_candidates(since=since, name=name, by=by)
        if not rows:
            return None

        accounts: dict = {}
        for r in rows:
            author = (r.get("author_name") or "").strip()
            if not author:
                continue
            acc = accounts.get(author)
            if acc is None:
                acc = {"author": author, "engagement": 0, "post_count": 0, "posts": []}
                accounts[author] = acc
            eng = int(r.get("engagement") or 0)
            acc["engagement"] += eng
            acc["post_count"] += 1
            acc["posts"].append({
                "text": (r.get("text") or "").strip(),
                "url": r.get("url") or "",
                "published_at": r["published_at"].isoformat() if r.get("published_at") else None,
                "stance": (r.get("stance") or "").strip(),
                "sentiment_score": float(r["sentiment_score"]) if r.get("sentiment_score") is not None else None,
                "engagement": eng,
            })

        # 2) kara liste elemesi
        candidates = [a for a in accounts.values() if not is_blocklisted(a["author"])]
        if not candidates:
            return None

        # 3) min_posts elemesi (cok agresifse gevset)
        filtered = [a for a in candidates if a["post_count"] >= min_posts]
        if not filtered:
            filtered = candidates

        # 4) dev (outlier) hesap elemesi -- medyanin median_factor kati ustu
        engs = sorted(a["engagement"] for a in filtered)
        med = median(engs) if engs else 0
        if med and med > 0:
            non_giants = [a for a in filtered if a["engagement"] <= med * median_factor]
        else:
            non_giants = filtered
        if not non_giants:
            non_giants = filtered

        # 5) orta seviye: etkilesime gore artan sirala, medyan konumunu sec
        non_giants.sort(key=lambda a: a["engagement"])
        chosen = non_giants[len(non_giants) // 2]

        posts = sorted(chosen["posts"], key=lambda p: p["engagement"], reverse=True)[:max_posts_for_llm]
        for p in posts:
            p["text"] = p["text"][:280]

        return {
            "author": chosen["author"],
            "engagement": int(chosen["engagement"]),
            "post_count": int(chosen["post_count"]),
            "candidate_count": len(non_giants),
            "median_engagement": int(med or 0),
            "posts": posts,
        }

    async def subject_hourly(self, *, since: datetime, name: str, by: str) -> List[dict]:
        """Tek konu/ozne icin saatlik mention sayilari (sparkline)."""
        col = self._subject_col(by)
        bucket = func.date_trunc("hour", Content.published_at).label("bucket")
        mention_count = func.count(ContentLabel.content_id)
        stmt = (
            select(bucket, mention_count.label("cnt"))
            .join(Content, Content.id == ContentLabel.content_id)
            .where(
                Content.platform.in_(_X_PLATFORMS),
                Content.published_at >= since,
                col == name,
            )
            .group_by(bucket)
            .order_by(bucket)
        )
        res = await self._session.execute(stmt)
        out: List[dict] = []
        for row in res.mappings().all():
            b = row["bucket"]
            out.append({
                "bucket": b.isoformat() if hasattr(b, "isoformat") else str(b),
                "cnt": int(row["cnt"]),
            })
        return out
