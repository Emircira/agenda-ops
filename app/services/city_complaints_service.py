"""Bölge şikâyet/kriz hattı: kaynak derlemesi, süzgeç analizi ve özet saklama."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.tr_provinces import TR_PROVINCES_81
from app.models.core import ComplaintsRadarCache
from app.providers.rss_provider import RSSProvider
from app.services.gemini_service import GeminiAIClient

COMPLAINTS_RADAR_CACHE_TTL_HOURS = 1

_ALLOWED_CITIES = frozenset(TR_PROVINCES_81) | {"Türkiye", "Türkiye Geneli"}


def _normalize_key(label: str) -> str:
    if not (label or "").strip():
        return "turkiye_geneli"
    t = str(label).strip()
    t = (
        t.replace("İ", "i")
        .replace("I", "ı")
        .replace("Ş", "ş")
        .replace("Ğ", "ğ")
        .replace("Ü", "ü")
        .replace("Ö", "ö")
        .replace("Ç", "ç")
        .lower()
    )
    return t.strip() or "turkiye_geneli"


def _cache_row_fresh(row: ComplaintsRadarCache) -> bool:
    if not row or not row.cached_at:
        return False
    ts = row.cached_at
    if getattr(ts, "tzinfo", None) is not None:
        ts = ts.replace(tzinfo=None)
    delta = datetime.utcnow() - ts
    return delta.total_seconds() < COMPLAINTS_RADAR_CACHE_TTL_HOURS * 3600


def _resolve_city_label(path_city: str) -> str:
    c = (path_city or "").strip()
    if not c:
        return "Türkiye"
    if c in _ALLOWED_CITIES:
        return c
    nk = _normalize_key(c)
    for p in TR_PROVINCES_81:
        if _normalize_key(p) == nk:
            return p
    if nk == _normalize_key("Türkiye") or nk == _normalize_key("Türkiye Geneli"):
        return "Türkiye"
    return ""


async def _fetch_cache_row(db: AsyncSession, province_key: str):
    try:
        res = await db.execute(
            select(ComplaintsRadarCache).where(ComplaintsRadarCache.province_key == province_key)
        )
        return res.scalar_one_or_none(), None
    except ProgrammingError as e:
        await db.rollback()
        low = str(getattr(e, "orig", e)).lower()
        if "complaints_radar_cache" in low and ("does not exist" in low or "undefinedtable" in low):
            return None, (
                "Bu modül için veri katmanı henüz hazır değil. Sistem yöneticisi yapılandırmasını tamamlamalıdır."
            )
        raise


async def _persist_cache(
    db: AsyncSession,
    province_key: str,
    province_label: str,
    payload: dict[str, Any],
) -> None:
    stmt = (
        pg_insert(ComplaintsRadarCache)
        .values(
            province_key=province_key,
            province_label=province_label,
            cached_at=datetime.utcnow(),
            payload_json=payload,
        )
        .on_conflict_do_update(
            index_elements=["province_key"],
            set_={
                "province_label": province_label,
                "cached_at": datetime.utcnow(),
                "payload_json": payload,
            },
        )
    )
    await db.execute(stmt)
    await db.commit()


async def run_city_complaints_pipeline(city_path: str, db: AsyncSession) -> dict[str, Any]:
    """
    GET /api/v1/contents/city-complaints/{city} için tam akış.
    """
    from fastapi import HTTPException

    label = _resolve_city_label(city_path)
    if not label:
        raise HTTPException(
            status_code=400,
            detail="Geçersiz il adı. Listeden seçilen 81 ilden biri veya Türkiye kullanın.",
        )

    province_key = _normalize_key(label)
    row, schema_err = await _fetch_cache_row(db, province_key)
    if schema_err:
        return {"success": False, "error": schema_err}

    if row and _cache_row_fresh(row):
        payload = row.payload_json or {}
        return {
            "success": True,
            "cached": True,
            "province_label": row.province_label,
            "analysis": payload.get("analysis", ""),
            "rss_count": payload.get("rss_count", 0),
            "x_count": payload.get("x_count", 0),
        }

    query_city = "Türkiye" if label in ("Türkiye Geneli", "Türkiye") else label

    rss_provider = RSSProvider()
    rss_data = await rss_provider.fetch_google_news_city_news(query_city, max_items=15)

    x_data: list = []
    try:
        from app.workers.ingest_tasks import get_x_provider
        xp = get_x_provider()
        x_data = await asyncio.wait_for(
            xp.fetch_city_complaints_posts(query_city, limit=20),
            timeout=15.0,
        )
    except asyncio.TimeoutError:
        logger.warning(f"Şehir radarı X zaman aşımı (15s) ({query_city}), yalnız RSS ile devam.")
        x_data = []
    except Exception as e:
        logger.warning(f"Şehir radarı X atlandı ({query_city}): {e}")
        x_data = []

    client = GeminiAIClient()
    analysis = await client.analyze_city_complaints_hybrid(label, rss_data, x_data)

    payload_out = {
        "analysis": analysis,
        "rss_count": len(rss_data),
        "x_count": len(x_data),
        "rss_samples": [r.get("text", "")[:400] for r in rss_data[:10]],
        "x_samples": [(p.get("text") or "")[:400] for p in x_data[:15]],
    }

    await _persist_cache(db, province_key, label, payload_out)

    return {
        "success": True,
        "cached": False,
        "province_label": label,
        "analysis": analysis,
        "rss_count": len(rss_data),
        "x_count": len(x_data),
    }
