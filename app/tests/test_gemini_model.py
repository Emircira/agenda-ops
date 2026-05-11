from app.services.gemini_model import choose_gemini_model


def test_auto_selects_preferred_available_model():
    assert choose_gemini_model("auto", ["gemini-2.5-flash-lite", "gemini-1.5-flash"]) == "gemini-2.5-flash-lite"


def test_unsupported_configured_model_falls_back_to_available_candidate():
    assert choose_gemini_model("gemini-1.5-flash", ["gemini-2.5-flash"]) == "gemini-2.5-flash"


def test_configured_model_is_kept_when_available():
    assert choose_gemini_model("gemini-2.5-pro", ["gemini-2.5-flash", "gemini-2.5-pro"]) == "gemini-2.5-pro"
