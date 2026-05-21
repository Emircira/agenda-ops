"""Seçim radarı REST uçları — YSK / TÜİK / Vikipedi birleşik coğrafi sentez."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.repositories.deps import get_election_repository
from app.repositories.election_repository import ElectionRepository
from app.services.election_geo_synthesis_service import build_geo_synthesis

router = APIRouter(tags=["Seçim Veritabanı"])


@router.get("/geo-synthesis")
async def get_geo_synthesis(
    province: str = Query(..., min_length=1, description="İl adı (örn. Ankara)"),
    district: str = Query("", description="İlçe (boş = il geneli)"),
    erepo: ElectionRepository = Depends(get_election_repository),
):
    """
    YSK (2009–2024 yerel), TÜİK (veritabanı + JSON) ve ``historical_context`` içinde Vikipedi
    giriş özeti (düz metin, kısaltılmış) birleşir. Nişanyan bu uçta kullanılmaz.
    """

    return await build_geo_synthesis(
        erepo=erepo,
        nrepo=None,
        province=province,
        district=district,
        scraper_call=None,
    )
