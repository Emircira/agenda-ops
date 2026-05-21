"""İstihbarat kaynakları (IntelligenceSource) CRUD — `/api/v1/sources`."""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException

from app.models.core import IntelligenceSource
from app.repositories.deps import get_source_repository
from app.repositories.source_repository import SourceRepository
from app.schemas.core import (
    ALLOWED_INTEL_SOURCE_CATEGORIES,
    IntelligenceSourceCreate,
    IntelligenceSourcePatch,
    IntelligenceSourceResponse,
)

router = APIRouter()

# Depolama: işçilerin beklediği ayrıntılı türler + youtube/rss/scraper
_ALLOWED_STORAGE_TYPES = frozenset(
    {
        "rss",
        "youtube",
        "scraper",
        "twitter_self",
        "twitter_competitor",
        "twitter_agency",
        "twitter_trend",
        "x",
    }
)


def _normalize_source_type(raw: str) -> str:
    t = (raw or "").strip().lower()
    if t == "twitter":
        return "twitter_self"
    return t


def _validate_category(cat: str) -> str:
    c = (cat or "general_agenda").strip()
    if c not in ALLOWED_INTEL_SOURCE_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"Geçersiz source_category. İzin verilenler: {', '.join(sorted(ALLOWED_INTEL_SOURCE_CATEGORIES))}",
        )
    return c


@router.get("", response_model=List[IntelligenceSourceResponse])
async def list_sources(repo: SourceRepository = Depends(get_source_repository)):
    return await repo.list_all_order_desc_id()


@router.post("", response_model=IntelligenceSourceResponse, status_code=201)
async def create_source(
    body: IntelligenceSourceCreate,
    repo: SourceRepository = Depends(get_source_repository),
):
    nt = _normalize_source_type(body.source_type)
    if nt not in _ALLOWED_STORAGE_TYPES:
        raise HTTPException(status_code=400, detail="Geçersiz source_type.")
    cat = _validate_category(body.source_category)
    row = IntelligenceSource(
        name=body.name.strip(),
        url_or_handle=body.url_or_handle.strip(),
        source_type=nt,
        domain=(body.domain or "general").strip() or "general",
        source_category=cat,
        is_active=body.is_active,
    )
    return await repo.create_and_refresh(row)


@router.patch("/{source_id}", response_model=IntelligenceSourceResponse)
async def patch_source(
    source_id: int,
    body: IntelligenceSourcePatch,
    repo: SourceRepository = Depends(get_source_repository),
):
    row = await repo.get_by_id(source_id)
    if not row:
        raise HTTPException(status_code=404, detail="Kaynak bulunamadı.")
    data = body.model_dump(exclude_unset=True)
    if "source_type" in data and data["source_type"] is not None:
        nt = _normalize_source_type(data["source_type"])
        if nt not in _ALLOWED_STORAGE_TYPES:
            raise HTTPException(status_code=400, detail="Geçersiz source_type.")
        row.source_type = nt
    if "name" in data and data["name"] is not None:
        row.name = data["name"].strip()
    if "url_or_handle" in data and data["url_or_handle"] is not None:
        row.url_or_handle = data["url_or_handle"].strip()
    if "is_active" in data and data["is_active"] is not None:
        row.is_active = data["is_active"]
    if "domain" in data and data["domain"] is not None:
        row.domain = (data["domain"] or "general").strip() or "general"
    if "source_category" in data and data["source_category"] is not None:
        row.source_category = _validate_category(data["source_category"])
    await repo.commit()
    out = await repo.get_by_id(source_id)
    if not out:
        raise HTTPException(status_code=404, detail="Kaynak bulunamadı.")
    return out


@router.delete("/{source_id}", status_code=204)
async def delete_source(
    source_id: int,
    repo: SourceRepository = Depends(get_source_repository),
):
    ok = await repo.delete_source_cascade_by_id(source_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Kaynak bulunamadı.")
