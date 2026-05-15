"""OSINT API — RAG ve vektör destekli sorgular."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.rag_service import analyze_topic_with_rag

router = APIRouter()


@router.get("/rag-search")
async def rag_search(
    query: str = Query(..., min_length=1, description="RAG araştırma konusu"),
    limit: int = Query(30, ge=1, le=100, description="En fazla kaç içerik bağlama dahil edilsin"),
    db: AsyncSession = Depends(get_db),
):
    """
    Konuyu vektör sorgusu olarak işler, benzer içerikleri bulur,
    Gemini ile OSINT özet raporu döner.
    """
    return await analyze_topic_with_rag(query=query, db=db, limit=limit)
