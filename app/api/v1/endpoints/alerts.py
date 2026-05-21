"""REST: sistem bildirimleri (okunmamış uyarılar)."""

from fastapi import APIRouter, Depends, HTTPException

from app.repositories.alert_repository import AlertRepository
from app.repositories.deps import get_alert_repository
from app.schemas.core import UnreadAlertsResponse

router = APIRouter(tags=["Bildirimler"])


@router.get("/unread", response_model=UnreadAlertsResponse)
async def list_unread_alerts(
    limit: int = 50,
    repo: AlertRepository = Depends(get_alert_repository),
) -> UnreadAlertsResponse:
    rows = await repo.list_unread_order_desc(limit=limit)
    count = await repo.count_unread()
    return UnreadAlertsResponse(count=count, alerts=rows)


@router.patch("/mark-read/{alert_id}")
async def mark_alert_read(
    alert_id: int,
    repo: AlertRepository = Depends(get_alert_repository),
) -> dict:
    updated = await repo.mark_read(alert_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Bildirim bulunamadı veya zaten okundu.")
    await repo.commit()
    return {"success": True, "id": alert_id}
