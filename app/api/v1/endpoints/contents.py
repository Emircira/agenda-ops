from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from app.db.session import get_db
from app.models.core import Content

router = APIRouter()

@router.get("/")
async def get_analyzed_contents(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Content).options(joinedload(Content.labels)).order_by(Content.id.desc()).limit(300)
    )
    contents = result.scalars().unique().all()
    
    return [
        {
            "id": str(c.id),
            "video_basligi": c.text.split('\n')[0].replace("BAŞLIK: ", "") if c.text else "Başlık Yok",
            "ai_konu": c.labels.topic if c.labels else "Analiz Bekleniyor",
            "halkin_tavri": c.labels.stance if c.labels else "Belirlenmedi",
            "ai_analiz_ve_halkin_sesi": c.labels.summary if c.labels else "Henüz yorum analizi yapılmadı",
            "ham_veri_ve_yorumlar": c.text, # Yorumlar inmiş mi buradan bakabilirsin
            "tarih": c.published_at
        } for c in contents
    ]