from fastapi import APIRouter, Depends

from app.repositories.deps import get_content_repository, get_complaint_cache_repository
from app.repositories.content_repository import ContentRepository
from app.repositories.complaint_cache_repository import ComplaintCacheRepository
from app.services.city_complaints_service import run_city_complaints_pipeline

router = APIRouter()


@router.get("/city-news/{city}")
async def city_complaints_radar(
    city: str,
    cache_repo: ComplaintCacheRepository = Depends(get_complaint_cache_repository),
):
    """İl bazlı şikâyet/kriz özeti — çoklu kaynak derlemesi ve özet önbelleği."""
    return await run_city_complaints_pipeline(city, cache_repo)


@router.get("/")
async def get_analyzed_contents(
    content_repo: ContentRepository = Depends(get_content_repository),
):
    contents = await content_repo.list_recent_with_labels(300)

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
