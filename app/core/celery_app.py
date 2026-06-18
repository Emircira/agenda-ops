import os
from celery import Celery
from celery.schedules import crontab
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
logger.info(f"\U0001F41D Celery baslatiliyor: Broker={redis_url}")

celery_app = Celery(
    "agenda_ops",
    broker=redis_url,
    backend=redis_url,
    # Gorev modulleri worker ayaga kalkarken kesin yuklensin (KeyError onleme)
    include=[
        "app.workers.ingest_tasks",
        "app.workers.labeling_tasks",
        "app.workers.scoring_tasks",
        "app.workers.macro_tasks",
        "app.workers.cleanup_tasks",
        "app.workers.radar_tasks",
    ],
)

celery_app.conf.update(
    timezone="Europe/Istanbul",
    enable_utc=False,
    task_default_queue="celery",
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    worker_prefetch_multiplier=1,
    task_acks_late=True,              # Worker cokerse gorev kaybolmasin
    worker_max_tasks_per_child=50,    # Bellek sizintisini onle
    # ─── GLOBAL RETRY & TIMEOUT AYARLARI ───
    task_soft_time_limit=600,         # 10 dakika soft limit
    task_time_limit=900,              # 15 dakika hard limit
    task_reject_on_worker_lost=True,  # Worker cokerse gorev tekrar kuyruga
)

# --- ZAMANLANMIS GOREVLER (CELERY BEAT) ---
celery_app.conf.beat_schedule = {
    # ─── VERI TOPLAMA ───
    # Her fetch gorevi tamamlandiginda otomatik olarak AI analizini tetikler (chain)
    # Asagidaki zamanlama sadece FETCH gorevleri icindir.
    "ingest-rss-hourly": {
        "task": "ingest_rss_all_sources",
        "schedule": crontab(minute="0", hour="*"),  # Her saat basi
    },
    "ingest-youtube-hourly": {
        "task": "ingest_youtube_all_sources",
        "schedule": crontab(minute="20", hour="*"),  # Her saat 20. dakika
    },
    # ─── TWITTER: 15 DAKIKADA BIR ───
    "ingest-x-sources-15m": {
        "task": "ingest_x_all_sources",
        "schedule": crontab(minute="*/15"),  # Her 15 dakikada bir
    },
    # X trend ornekleme: API maliyeti — en fazla 6 saatte bir (reply/derin tarama yok)
    "ingest-x-trends-6h": {
        "task": "ingest_x_daily_trends",
        "schedule": crontab(minute="10", hour="*/6"),  # 00:10, 06:10, 12:10, 18:10
    },
    # ─── YAPAY ZEKA ANALIZ (Guvenlik agi — chain tetiklenmezse bile calisir) ───
    "batch-analyze-catchall": {
        "task": "batch_analyze_contents",
        "schedule": crontab(minute="45", hour="*"),  # Her saat 45. dakika (catch-all)
    },
    # ─── VERITABANI BAKIM (retention) ───
    "cleanup-intelligence-nightly": {
        "task": "cleanup_old_intelligence_data",
        "schedule": crontab(minute=0, hour=3),  # Her gun 03:00 (Europe/Istanbul)
    },
    # ─── FAZ 4.7: Canli makro + Karargah icgoru — her 6 saatte (tam saat) ───
    "macro-real-data-6h": {
        "task": "fetch_real_macro_data",
        "schedule": crontab(minute="0", hour="*/6"),
    },
    "karargah-grievances-demands-6h": {
        "task": "fetch_grievances_and_demands",
        "schedule": crontab(minute="0", hour="*/6"),
    },
    # ─── RADAR: metrik snapshot (raw_json -> content_metrics) — saatlik :50 ───
    # 00:30'daki compute_radar_daily'den once gece de calisarak dunun metriklerini hazirlar.
    "metrics-snapshot-hourly": {
        "task": "snapshot_content_metrics",
        "schedule": crontab(minute="50", hour="*"),  # Her saat 50. dakika
    },
    # ─── RADAR: gunluk etkilesim skoru (bir onceki gunu hesaplar) ───
    "radar-daily-0030": {
        "task": "compute_radar_daily",
        "schedule": crontab(minute=30, hour=0),  # Her gun 00:30 (Europe/Istanbul)
    },
}

# NOT: Gorev modulleri yukarida Celery(include=[...]) ile dogrudan ice aktarilir.
# autodiscover_tasks() paket bekler ve her paketin sonuna ".tasks" ekler; modul
# yollari (or. "app.workers.ingest_tasks") verildiginde gorevler KAYIT OLMAZ.
# Bu yuzden include= kullaniyoruz.