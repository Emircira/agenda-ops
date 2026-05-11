from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from typing import List
from uuid import UUID
from datetime import datetime
import pytz

from app.db.session import get_db
from app.models.core import Source, Content, Opportunity
from app.schemas.core import SourceCreate, SourceResponse, OpportunityResponse, ContentResponse

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
async def create_source(source: SourceCreate, db: AsyncSession = Depends(get_db)):
    """Yeni bir istihbarat kaynağı (RSS, YouTube, X) ekler."""
    new_source = Source(**source.model_dump())
    db.add(new_source)
    await db.commit()
    await db.refresh(new_source)
    return new_source

@router.get("/sources", response_model=List[SourceResponse])
async def get_sources(db: AsyncSession = Depends(get_db)):
    """Aktif istihbarat kaynaklarını listeler."""
    result = await db.execute(select(Source))
    return result.scalars().all()

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
async def get_opportunities(limit: int = 10, db: AsyncSession = Depends(get_db)):
    """Siyasi hedefin önüne çıkacak en sıcak fırsat kartlarını getirir."""
    result = await db.execute(select(Opportunity).order_by(desc(Opportunity.score)).limit(limit))
    return result.scalars().all()

@router.get("/contents/{id}", response_model=ContentResponse)
async def get_content(id: UUID, db: AsyncSession = Depends(get_db)):
    """Ham içeriğin detaylarını ID (UUID) ile getirir."""
    result = await db.execute(select(Content).where(Content.id == id))
    content = result.scalar_one_or_none()
    if not content:
        raise HTTPException(status_code=404, detail="İçerik Karargahta bulunamadı")
    return content

@router.get("/reports/daily")
async def get_daily_report(db: AsyncSession = Depends(get_db)):
    """Günlük İstihbarat Briefing Raporu (Dashboard Uyumlu)"""
    result = await db.execute(select(Opportunity).order_by(desc(Opportunity.score)).limit(3))
    top_opps = result.scalars().all()
    
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