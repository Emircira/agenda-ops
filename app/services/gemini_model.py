import os
from functools import lru_cache
from typing import Iterable

import google.generativeai as genai
from loguru import logger


DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
PREFERRED_GEMINI_MODELS = (
    DEFAULT_GEMINI_MODEL,
    "gemini-2.5-flash-lite",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
)


def _normalize_model_name(model_name: str | None) -> str:
    value = (model_name or "").strip()
    if value.startswith("models/"):
        return value.split("/", 1)[1]
    return value


def _model_supports_generate_content(model) -> bool:
    methods = getattr(model, "supported_generation_methods", None) or []
    return "generateContent" in methods


def _candidate_models() -> tuple[str, ...]:
    raw = os.getenv("GEMINI_MODEL_CANDIDATES", "")
    env_candidates = tuple(
        _normalize_model_name(item)
        for item in raw.split(",")
        if item.strip()
    )
    return env_candidates or PREFERRED_GEMINI_MODELS


@lru_cache(maxsize=1)
def available_generate_content_models() -> tuple[str, ...]:
    """API hesabında generateContent destekleyen modelleri döndürür."""
    try:
        models = []
        for model in genai.list_models():
            if _model_supports_generate_content(model):
                models.append(_normalize_model_name(getattr(model, "name", "")))
        return tuple(model for model in models if model)
    except Exception as exc:
        logger.warning(f"Gemini model listesi alınamadı, varsayılan seçim kullanılacak: {exc}")
        return ()


def choose_gemini_model(configured_model: str | None = None, available_models: Iterable[str] | None = None) -> str:
    """
    Geçerli Gemini modelini seçer.

    `GEMINI_MODEL_NAME=auto` veya boş değerlerde, hesabın desteklediği modeller
    arasından tercih sırasına göre seçim yapılır. Yanlış/eskimiş bir model adı
    verilirse ve model listesi alınabiliyorsa otomatik olarak çalışan adaya düşer.
    """
    configured = _normalize_model_name(configured_model or os.getenv("GEMINI_MODEL_NAME", "auto"))
    available = tuple(_normalize_model_name(model) for model in (available_models or available_generate_content_models()))

    if configured and configured.lower() not in {"auto", "default"}:
        if not available or configured in available:
            return configured
        logger.warning(
            f"GEMINI_MODEL_NAME={configured} generateContent listesinde yok; "
            "otomatik uygun model seçilecek."
        )

    for candidate in _candidate_models():
        if not available or candidate in available:
            return candidate

    return available[0] if available else DEFAULT_GEMINI_MODEL


def create_gemini_model(api_key: str | None = None):
    """Gemini istemcisini configure eder ve seçilen GenerativeModel'i döndürür."""
    key = api_key or os.getenv("GEMINI_API_KEY")
    if not key:
        logger.error("GEMINI_API_KEY bulunamadı!")
        return None, None

    genai.configure(api_key=key)
    model_name = choose_gemini_model()
    logger.info(f"✅ Gemini modeli seçildi: {model_name}")
    return genai.GenerativeModel(model_name), model_name
