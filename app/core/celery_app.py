import os
from celery import Celery
from celery.schedules import crontab
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")

logger.info(f"🐝 Celery başlatılıyor: Broker={redis_url}")

celery_app = Celery(
    "agenda_ops",
    broker=redis_url,
    backend=redis_url,
)

celery_app.conf.update(
    timezone="Europe/Istanbul",
    enable_utc=False,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    worker_prefetch_multiplier=1,
    task_acks_late=True,              # Worker çökerse görev kaybolmasın
    worker_max_tasks_per_child=50,    # Bellek sızıntısını önle

    # ─── GLOBAL RETRY & TIMEOUT AYARLARI ───
    task_soft_time_limit=600,         # 10 dakika soft limit
    task_time_limit=900,              # 15 dakika hard limit
    task_reject_on_worker_lost=True,  # Worker çökerse görev tekrar kuyruğa
)

# --- ZAMANLANMIŞ GÖREVLER (CELERY BEAT) ---
celery_app.conf.beat_schedule = {
    # ─── VERİ TOPLAMA ───
    # Her fetch görevi tamamlandığında otomatik olarak AI analizini tetikler (chain)
    # Aşağıdaki zamanlama sadece FETCH görevleri içindir.
    "ingest-rss-hourly": {
        "task": "ingest_rss_all_sources",
        "schedule": crontab(minute="0", hour="*"),  # Her saat başı
    },
    "ingest-youtube-hourly": {
        "task": "ingest_youtube_all_sources",
        "schedule": crontab(minute="20", hour="*"),  # Her saat 20. dakika
    },

    # ─── TWITTER: 15 DAKİKADA BİR ───
    "ingest-x-sources-15m": {
        "task": "ingest_x_all_sources",
        "schedule": crontab(minute="*/15"),  # Her 15 dakikada bir
    },
    "ingest-x-trends-hourly": {
        "task": "ingest_x_daily_trends",
        "schedule": crontab(minute="10", hour="*"),  # Her saat 10. dakika
    },

    # ─── YAPAY ZEKA ANALİZ (Güvenlik ağı — chain tetiklenmezse bile çalışır) ───
    "batch-analyze-catchall": {
        "task": "batch_analyze_contents",
        "schedule": crontab(minute="45", hour="*"),  # Her saat 45. dakika (catch-all)
    },

    # ─── VERİTABANI BAKIM ───
    "cleanup-db-daily": {
        "task": "cleanup_old_content",
        "kwargs": {"days": 30},
        "schedule": crontab(minute="0", hour="0"),  # Her gece yarısı
    },
}

# Task'lerin bulunduğu tüm klasörleri Celery'e tanıt
celery_app.autodiscover_tasks([
    "app.workers.ingest_tasks",
    "app.workers.labeling_tasks",
    "app.workers.scoring_tasks"
])