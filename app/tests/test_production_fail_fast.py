import pytest

from app.providers.x_provider import RapidXProvider
from app.providers.youtube_provider import YouTubeProvider
from app.services.labeling_service import LabelingService


def test_x_provider_requires_real_api_key(monkeypatch):
    monkeypatch.delenv("RAPIDAPI_KEY", raising=False)
    monkeypatch.delenv("RAPID_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="RAPIDAPI_KEY"):
        RapidXProvider()


def test_youtube_provider_requires_real_api_key(monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="YOUTUBE_API_KEY"):
        YouTubeProvider()


def test_labeling_service_does_not_emit_deterministic_fallback(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    service = LabelingService()
    with pytest.raises(RuntimeError, match="sahte"):
        service.analyze_content("enflasyon ve zam hakkında gerçek içerik", "twitter")


def test_content_model_defines_labels_for_contents_api_joinedload():
    """GET /api/v1/contents/ joinedload(Content.labels) — Content'ta ilişki yoksa AttributeError (500)."""
    from sqlalchemy import select
    from sqlalchemy.orm import joinedload

    from app.models.core import Content

    select(Content).options(joinedload(Content.labels))
