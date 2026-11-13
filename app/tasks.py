from __future__ import annotations
from apscheduler.schedulers.background import BackgroundScheduler

from app.config import get_settings


def setup_scheduler() -> BackgroundScheduler:
    settings = get_settings()
    scheduler = BackgroundScheduler(timezone=settings.scheduled_sync_timezone)
    scheduler.add_job(
        "app.sync_service:run_scheduled_sync",
        "interval",
        hours=settings.scheduled_sync_interval_hours,
        kwargs={"months": 1, "max_pages": 24, "max_items": 180},
        id="hourly_news_sync",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
        replace_existing=True,
    )
    if settings.werss_database_path:
        scheduler.add_job("app.materials:sync_materials", "interval", minutes=15,
                          id="werss_materials_import", max_instances=1, coalesce=True)
    return scheduler
