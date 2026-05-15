"""RAG: vektör araması + Gemini ile istihbarat özeti."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.embedding_service import generate_embedding
from app.services.gemini_service import GeminiAIClient
from app.repositories.vector_repository import VectorRepository


def _strip_for_json(text: str) -> str:
    if not text:
        return ""
    t = text.strip()
    for fence in ("```json", "```JSON", "```"):
        t = t.replace(fence, "")
    return t.strip()


def _parse_json_object(text: str) -> Dict[str, Any] | None:
    t = _strip_for_json(text)
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(t[start : end + 1])
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


async def analyze_topic_with_rag(
    query: str,
    db: AsyncSession,
    limit: int = 30,
) -> Dict[str, Any]:
    """
    Konu sorgusunu retrieval_query ile vektörleştirir, pgvector ile benzer içerikleri bulur,
    Gemini ile OSINT raporu üretir. db: AsyncSession (FastAPI / servis katmanı).
    """
    q = (query or "").strip()
    if not q:
        return {"success": False, "error": "Sorgu boş olamaz.", "query": query}

    lim = max(1, min(int(limit), 100))

    try:
        query_vec = generate_embedding(q, task_type="retrieval_query")
    except Exception as e:
        logger.warning(f"RAG: sorgu embedding üretilemedi: {e}")
        return {
            "success": False,
            "error": f"Sorgu vektörleştirilemedi: {e}",
            "query": q,
        }

    vec_repo = VectorRepository(db)
    try:
        matches = await vec_repo.search_similar_contents(query_vec, limit=lim)
    except Exception as e:
        logger.exception("RAG: search_similar_contents")
        return {
            "success": False,
            "error": f"Vektör araması başarısız: {e}",
            "query": q,
        }

    if not matches:
        return {
            "success": True,
            "query": q,
            "match_count": 0,
            "report": None,
            "message": "Veritabanında bu konu için vektörü olan eşleşen içerik bulunamadı.",
        }

    evidence_lines: List[str] = []
    meta_rows: List[Dict[str, Any]] = []
    for idx, (content, distance) in enumerate(matches, 1):
        snippet = (content.text or "").strip().replace("\n", " ")
        if len(snippet) > 900:
            snippet = snippet[:900] + "…"
        evidence_lines.append(
            f"[{idx}] platform={content.platform} | cosine_dist={distance:.4f} | {snippet}"
        )
        meta_rows.append(
            {
                "content_id": str(content.id),
                "platform": content.platform,
                "distance": float(distance),
                "preview": snippet[:240],
            }
        )

    evidence_blob = "\n".join(evidence_lines)

    instructions = f"""Sen bir OSINT analistisin. Kullanıcının araştırdığı konu: {q}.
Sana bu konuyla anlamsal olarak en çok eşleşen sahadaki ham verileri veriyorum.
Bu verileri okuyarak konu hakkında genel kitle duygu durumunu ve ana riskleri özetleyen bir istihbarat raporu üret.

HAM VERİLER (benzerlik sırasıyla):
{evidence_blob}

Yanıtı YALNIZCA geçerli bir JSON nesnesi olarak ver (markdown veya code fence yok). Şema:
{{
  "executive_summary": "2-4 cümle üst özet",
  "sentiment_overview": "Kitle tonu ve kutuplaşma",
  "main_risks": ["risk1", "risk2"],
  "recommended_monitoring": "İzlenecek sinyaller",
  "confidence_notes": "Veri kalitesi / sınırlılıklar"
}}"""

    client = GeminiAIClient()
    if not client.model:
        return {
            "success": False,
            "query": q,
            "match_count": len(matches),
            "evidence": meta_rows,
            "error": "Gemini modeli kullanılamıyor (GEMINI_API_KEY?).",
        }

    try:
        raw = await client.generate_content_async(instructions)
    except Exception as e:
        logger.warning(f"RAG: Gemini çağrısı başarısız: {e}")
        return {
            "success": False,
            "query": q,
            "match_count": len(matches),
            "evidence": meta_rows,
            "error": str(e),
        }

    parsed = _parse_json_object(raw)
    if parsed is None:
        return {
            "success": True,
            "query": q,
            "match_count": len(matches),
            "evidence": meta_rows,
            "report": {"raw_text": raw, "parse_ok": False},
            "message": "Model yanıtı JSON olarak çözümlenemedi; ham metin report.raw_text içinde.",
        }

    return {
        "success": True,
        "query": q,
        "match_count": len(matches),
        "evidence": meta_rows,
        "report": parsed,
    }
