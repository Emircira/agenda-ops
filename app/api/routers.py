from fastapi import APIRouter, Depends, HTTPException
from typing import List
from uuid import UUID
from datetime import datetime
import pytz

from app.models.core import Opportunity
from app.schemas.core import OpportunityResponse, ContentResponse
from app.repositories.deps import (
    get_content_repository,
    get_opportunity_repository,
)
from app.repositories.content_repository import ContentRepository
from app.repositories.opportunity_repository import OpportunityRepository

# Tum Saha Ajanlarini (Worker) import ediyoruz
from app.workers.ingest_tasks import (
    ingest_rss_all_sources,
    ingest_youtube_all_sources,
    ingest_x_daily_trends,
    ingest_x_person_mention_posts
)

# Radar modulu API uclari (GET /api/v1/radar, POST /api/v1/radar/recompute)
from app.api.v1.endpoints import radar as radar_endpoints
# Gundem Nabzi API ucu (GET /api/v1/pulse/agenda)
from app.api.v1.endpoints import pulse as pulse_endpoints

# Swagger arayuzunde temiz gorunmesi icin tag ekledik
router = APIRouter(tags=["Karargah Operasyonlari"])


@router.post("/ingest/run")
async def run_ingestion():
    """Tum botlari (Saha Ajanlarini) manuel tetikler. Celery arka planda asenkron calisir."""
    ingest_rss_all_sources.delay()
    ingest_youtube_all_sources.delay()
    
    # Eksik olan X/Twitter botlari Karargah'a baglandi
    ingest_x_daily_trends.delay()
    ingest_x_person_mention_posts.delay(target_person="Siyasi Lider") 
    
    return {"status": "success", "message": "Otonom veri toplama harekati (RSS, YouTube, X) baslatildi."}

@router.get("/opportunities", response_model=List[OpportunityResponse])
async def get_opportunities(
    limit: int = 10,
    opportunity_repo: OpportunityRepository = Depends(get_opportunity_repository),
):
    """Siyasi hedefin onune cikacak en sicak firsat kartlarini getirir."""
    return await opportunity_repo.list_ordered_by_recency(limit)

@router.get("/contents/{id}", response_model=ContentResponse)
async def get_content(
    id: UUID,
    content_repo: ContentRepository = Depends(get_content_repository),
):
    """Ham icerigin detaylarini ID (UUID) ile getirir."""
    content = await content_repo.get_by_id(id)
    if not content:
        raise HTTPException(status_code=404, detail="Icerik Karargahta bulunamadi")
    return content

@router.get("/reports/daily")
async def get_daily_report(
    opportunity_repo: OpportunityRepository = Depends(get_opportunity_repository),
):
    """Gunluk Istihbarat Briefing Raporu (Dashboard Uyumlu)"""
    top_opps = await opportunity_repo.list_top_for_daily_briefing(3)
    
    brief = [f"[Skor: {opp.score}] {opp.topic} -> Kriz/Cerceve: {opp.frame}" for opp in top_opps]
    
    # Master Prompt zorunlulugu: Europe/Istanbul saat dilimi
    tz = pytz.timezone('Europe/Istanbul')
    ist_time = datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')
    
    return {
        "title": "KARARGAH GUNLUK BRIEFING",
        "date": ist_time,
        "top_opportunities": brief,
        "action_required": len(top_opps) > 0
    }


# Radar liderlik tablosu alt-router'i (/api/v1 altinda -> /api/v1/radar)
router.include_router(radar_endpoints.router, prefix="/radar", tags=["Radar"])
# Gundem Nabzi alt-router'i (/api/v1 altinda -> /api/v1/pulse)
router.include_router(pulse_endpoints.router, prefix="/pulse", tags=["Gundem Nabzi"])
