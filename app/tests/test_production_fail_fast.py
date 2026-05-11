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
