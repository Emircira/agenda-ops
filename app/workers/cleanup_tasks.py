"""Veri saklama (retention) — içerik, vektör ve anket temizliği."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta

from loguru import logger

from app.core.celery_app import celery_app
from app.db.session import AsyncSessionLocal
from app.repositories.content_repository import ContentRepository
from app.repositories.macro_repository import MacroRepository


def _run_async(coro):
    return asyncio.run(coro)


CONTENT_RETENTION_DAYS = 30
POLL_RETENTION_DAYS = 90


@celery_app.task(name="cleanup_old_intelligence_data", bind=True, max_retries=2)
def cleanup_old_intelligence_data(self):
    """
    - Content (+ ContentEmbedding, etiket, metrik, entity ilişkisi): published_at < (şimdi - 30 gün) olanlar silinir.
    - PollData: published_date < (şimdi - 90 gün) olanlar silinir.
    - MacroIndicator'a dokunulmaz.
    """

    async def _task() -> str:
        now = datetime.utcnow()
        content_cutoff = now - timedelta(days=CONTENT_RETENTION_DAYS)
        poll_cutoff = (now - timedelta(days=POLL_RETENTION_DAYS)).date()

        async with AsyncSessionLocal() as session:
            content_repo = ContentRepository(session)
            macro_repo = MacroRepository(session)

            n_content = await content_repo.delete_intelligence_content_published_before(content_cutoff)
            n_polls = await macro_repo.delete_poll_data_published_before(poll_cutoff)

            await content_repo.commit()

        logger.info(
            f"Temizlik tamamlandı: {n_content} içerik (ve bağlı vektör/etiket), {n_polls} anket satırı silindi."
        )
        return f"content={n_content}, polls={n_polls}"

    try:
        return _run_async(_task())
    except Exception as e:  # noqa: BLE001
        logger.exception(f"cleanup_old_intelligence_data başarısız: {e}")
        raise
