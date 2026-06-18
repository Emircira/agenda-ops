"""Sistem bakım uçları — retention tetikleme vb."""

from fastapi import APIRouter

from app.workers.cleanup_tasks import cleanup_old_intelligence_data

router = APIRouter()


@router.post("/trigger-cleanup")
async def trigger_intelligence_cleanup():
    """Geliştirici / operasyon: retention görevini anında kuyruğa atar."""
    cleanup_old_intelligence_data.delay()
    return {
        "success": True,
        "message": "Temizlik görevi (cleanup_old_intelligence_data) kuyruğa alındı.",
    }
