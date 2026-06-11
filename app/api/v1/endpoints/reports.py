"""REST: durum raporu (SITREP) - HTML (yazdir) veya JSON."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.templating import Jinja2Templates

from app.repositories.alert_repository import AlertRepository
from app.repositories.deps import (
    get_alert_repository,
    get_macro_repository,
    get_radar_repository,
)
from app.repositories.macro_repository import MacroRepository
from app.repositories.radar_repository import RadarRepository
from app.services.report_service import generate_daily_sitrep

router = APIRouter(tags=["Raporlar"])

templates = Jinja2Templates(directory="app/templates")

SitrepFormat = Literal["html", "json"]


@router.get("/sitrep")
async def get_daily_sitrep(
    request: Request,
    response_format: Annotated[SitrepFormat, Query(alias="format")] = "html",
    macro_repo: MacroRepository = Depends(get_macro_repository),
    alert_repo: AlertRepository = Depends(get_alert_repository),
    radar_repo: RadarRepository = Depends(get_radar_repository),
):
    """
    Gunluk durum ozeti (SITREP).

    - ``format=html`` (varsayilan): yazdirmaya uygun tek sayfa HTML.
    - ``format=json``: ham veri paketi (API / otomasyon).
    """
    report = await generate_daily_sitrep(macro_repo, alert_repo, radar_repo)
    if response_format == "json":
        return report
    return templates.TemplateResponse(
        "sitrep.html",
        {"request": request, "report": report},
    )
