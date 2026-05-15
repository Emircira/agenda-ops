import os
from functools import lru_cache
from typing import Any, Iterable, List, Optional

import google.generativeai as genai
from loguru import logger


DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

# Google prompt_feedback / boş candidate durumunda zinciri beslemek için ortak metin
GEMINI_BLOCKED_PLAIN_MESSAGE = (
    "İçerik Google güvenlik politikaları (PROHIBITED) nedeniyle analiz edilemedi."
)

PREFERRED_GEMINI_MODELS = (
    DEFAULT_GEMINI_MODEL,
    "gemini-2.5-flash-lite",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
)


def gemini_safety_settings_block_none() -> List[dict[str, Any]]:
    """
    OSINT / ham sosyal veri analizi için tüm harm kategorilerinde engeli kaldırır.
    """
    from google.generativeai.types import HarmBlockThreshold, HarmCategory

    return [
        {
            "category": HarmCategory.HARM_CATEGORY_HARASSMENT,
            "threshold": HarmBlockThreshold.BLOCK_NONE,
        },
        {
            "category": HarmCategory.HARM_CATEGORY_HATE_SPEECH,
            "threshold": HarmBlockThreshold.BLOCK_NONE,
        },
        {
            "category": HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
            "threshold": HarmBlockThreshold.BLOCK_NONE,
        },
        {
            "category": HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
            "threshold": HarmBlockThreshold.BLOCK_NONE,
        },
    ]


def extract_gemini_response_text(response: Any) -> Optional[str]:
    """
    response.text / parts okumadan önce blok ve boş candidate kontrolü.
    Blok veya okunamayan yanıtta None döner; terminalde prompt_feedback loglanır.
    """
    if response is None:
        return None

    pf = getattr(response, "prompt_feedback", None)
    block_reason = getattr(pf, "block_reason", None) if pf is not None else None
    if block_reason:
        logger.warning(f"GEMINI BLOCKED CONTENT: {pf}")
        return None

    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        logger.warning(
            f"GEMINI BLOCKED CONTENT: candidates boş; prompt_feedback={pf}"
        )
        return None

    try:
        text = response.text
    except (ValueError, AttributeError) as e:
        logger.warning(
            f"GEMINI BLOCKED CONTENT veya metin çıkarılamadı: {e}; "
            f"prompt_feedback={pf}"
        )
        return None

    if text is None:
        return None
    text = str(text).strip()
    return text if text else None


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
