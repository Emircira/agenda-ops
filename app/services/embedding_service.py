"""Gemini gemini-embedding-001 ile metin vektörleştirme (768 boyut).

Eski models/embedding-001 v1beta'ta artık yok (404). Aynı boyutta tutmak için
output_dimensionality=768 kullanılır. Veritabanında eski modelden kalan vektörler
varsa tam anlamıyla karşılaştırılabilir olmayabilir; gerekirse içerikler yeniden
indekslenmeli.
"""

import os
from typing import List

import google.generativeai as genai
from loguru import logger

from app.models.core import EMBEDDING_DIMENSION

# Ortam ile override: GEMINI_EMBEDDING_MODEL=models/gemini-embedding-001
_GEMINI_EMBEDDING_MODEL = os.getenv(
    "GEMINI_EMBEDDING_MODEL", "models/gemini-embedding-001"
)


def generate_embedding(
    text: str,
    task_type: str = "retrieval_document",
) -> List[float]:
    """
    Metni Gemini gemini-embedding-001 ile 768 boyutlu vektöre çevirir.
    - retrieval_document: DB'ye yazılacak içerikler
    - retrieval_query: kullanıcı / RAG arama sorgusu
    API anahtarı ortamda yoksa veya yanıt boşsa açık hata verir (sessiz fallback yok).
    """
    raw = (text or "").strip()
    if not raw:
        raise ValueError("Embedding için metin boş olamaz.")

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY tanımlı değil; embedding üretilemez.")

    genai.configure(api_key=api_key)
    result = genai.embed_content(
        model=_GEMINI_EMBEDDING_MODEL,
        content=raw,
        task_type=task_type,
        output_dimensionality=EMBEDDING_DIMENSION,
    )
    vec = result.get("embedding") if isinstance(result, dict) else None
    if not vec or not isinstance(vec, (list, tuple)):
        logger.error("Gemini embed_content beklenmeyen yanıt: embedding alanı yok")
        raise RuntimeError("Gemini embedding yanıtı geçersiz.")

    out = [float(x) for x in vec]
    if len(out) != EMBEDDING_DIMENSION:
        raise ValueError(
            f"Beklenen embedding boyutu {EMBEDDING_DIMENSION}, gelen: {len(out)}"
        )
    return out
