from datetime import datetime
from types import SimpleNamespace

from app.core.utils import content_quality_score, select_ai_triage_candidates


def test_triage_rejects_retweets_short_text_and_zero_social_engagement():
    assert content_quality_score("twitter", "RT @user kısa metin", {"metrics": {"likes": 10}}) == 0
    assert content_quality_score("twitter", "çok kısa", {"metrics": {"likes": 10}}) == 0
    assert content_quality_score("twitter", "Bu metin yeterince uzun ama etkileşimi olmadığı için elenir.", {"metrics": {"likes": 0, "retweets": 0, "replies": 0}}) == 0
    assert content_quality_score("youtube_comment", "Bu yorum anlamlı ve yeterince uzun bir toplumsal tepki içeriyor.", {"snippet": {"topLevelComment": {"snippet": {"likeCount": 0}}}}) == 0


def test_triage_scores_rich_rss_without_social_engagement():
    score = content_quality_score(
        "rss",
        "Ekonomi politikaları ve yerel yönetim gündemi hakkında kapsamlı haber metni.",
        {},
    )
    assert score > 0


def test_select_ai_triage_candidates_keeps_only_best_items():
    contents = [
        SimpleNamespace(platform="twitter", text="Zayıf ama uzun metin etkileşimsiz olduğu için elenmeli.", raw_json={"metrics": {"likes": 0}}, fetched_at=datetime(2026, 1, 1)),
        SimpleNamespace(platform="twitter", text="Güçlü ve zengin X içeriği yüksek etkileşim ile seçilmeli.", raw_json={"metrics": {"likes": 10, "retweets": 2, "replies": 1}}, fetched_at=datetime(2026, 1, 2)),
        SimpleNamespace(platform="rss", text="RSS haber metni detaylı ve AI sentezi için yeterli bağlam içeriyor.", raw_json={}, fetched_at=datetime(2026, 1, 3)),
    ]

    selected = select_ai_triage_candidates(contents, limit=2)
    assert len(selected) == 2
    assert contents[0] not in selected
