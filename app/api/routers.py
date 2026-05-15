from fastapi import APIRouter, Depends, HTTPException
from typing import List
from uuid import UUID
from datetime import datetime
import pytz

from app.models.core import Source, Opportunity
from app.schemas.core import SourceCreate, SourceResponse, OpportunityResponse, ContentResponse
from app.repositories.deps import (
    get_target_repository,
    get_content_repository,
    get_opportunity_repository,
)
from app.repositories.target_repository import TargetRepository
from app.repositories.content_repository import ContentRepository
from app.repositories.opportunity_repository import OpportunityRepository

# Tüm Saha Ajanlarını (Worker) import ediyoruz
from app.workers.ingest_tasks import (
    ingest_rss_all_sources,
    ingest_youtube_all_sources,
    ingest_x_daily_trends,
    ingest_x_person_mention_posts
)

# Swagger arayüzünde temiz görünmesi için tag ekledik
router = APIRouter(tags=["Karargah Operasyonları"])

@router.post("/sources", response_model=SourceResponse)
async def create_source(
    source: SourceCreate,
    target_repo: TargetRepository = Depends(get_target_repository),
):
    """Yeni bir istihbarat kaynağı (RSS, YouTube, X) ekler."""
    new_source = Source(**source.model_dump())
    return await target_repo.create_and_refresh(new_source)

@router.get("/sources", response_model=List[SourceResponse])
async def get_sources(target_repo: TargetRepository = Depends(get_target_repository)):
    """Aktif istihbarat kaynaklarını listeler."""
    return await target_repo.list_all()

@router.post("/ingest/run")
async def run_ingestion():
    """Tüm botları (Saha Ajanlarını) manuel tetikler. Celery arka planda asenkron çalışır."""
    ingest_rss_all_sources.delay()
    ingest_youtube_all_sources.delay()
    
    # Eksik olan X/Twitter botları Karargah'a bağlandı
    ingest_x_daily_trends.delay()
    ingest_x_person_mention_posts.delay(target_person="Siyasi Lider") 
    
    return {"status": "success", "message": "Otonom veri toplama harekatı (RSS, YouTube, X) başlatıldı."}

@router.get("/opportunities", response_model=List[OpportunityResponse])
async def get_opportunities(
    limit: int = 10,
    opportunity_repo: OpportunityRepository = Depends(get_opportunity_repository),
):
    """Siyasi hedefin önüne çıkacak en sıcak fırsat kartlarını getirir."""
    return await opportunity_repo.list_ordered_by_recency(limit)

@router.get("/contents/{id}", response_model=ContentResponse)
async def get_content(
    id: UUID,
    content_repo: ContentRepository = Depends(get_content_repository),
):
    """Ham içeriğin detaylarını ID (UUID) ile getirir."""
    content = await content_repo.get_by_id(id)
    if not content:
        raise HTTPException(status_code=404, detail="İçerik Karargahta bulunamadı")
    return content

@router.get("/reports/daily")
async def get_daily_report(
    opportunity_repo: OpportunityRepository = Depends(get_opportunity_repository),
):
    """Günlük İstihbarat Briefing Raporu (Dashboard Uyumlu)"""
    top_opps = await opportunity_repo.list_top_for_daily_briefing(3)
    
    brief = [f"[Skor: {opp.score}] {opp.topic} -> Kriz/Çerçeve: {opp.frame}" for opp in top_opps]
    
    # Master Prompt zorunluluğu: Europe/Istanbul saat dilimi
    tz = pytz.timezone('Europe/Istanbul')
    ist_time = datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')
    
    return {
        "title": "KARARGAH GÜNLÜK BRIEFING",
        "date": ist_time,
        "top_opportunities": brief,
        "action_required": len(top_opps) > 0
    }
