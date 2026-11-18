from __future__ import annotations
from apscheduler.schedulers.background import BackgroundScheduler

from app.config import get_settings


def setup_scheduler() -> BackgroundScheduler:
    import os
    settings = get_settings()
    scheduler = BackgroundScheduler(timezone=settings.scheduled_sync_timezone)
    if os.getenv('LOCAL_WERSS_HISTORY_WORKER_ENABLED') == '1' and settings.werss_database_path:
        scheduler.add_job('app.history_worker:run', 'interval', minutes=5,
                          id='local_werss_history', max_instances=1, coalesce=True)
    if os.getenv('OFFICIAL_WEBSITES_ENABLED')=='1':
        scheduler.add_job('app.official_sources:collect','interval',hours=2,id='official_website_supplement',max_instances=1,coalesce=True)
    if os.getenv('LEGACY_BODY_RECOVERY_ENABLED')=='1':
        scheduler.add_job('app.news_recovery:run','interval',hours=2,id='legacy_news_body_recovery',max_instances=1,coalesce=True)
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
    if settings.werss_api_url and settings.werss_api_token_file:
        scheduler.add_job('app.upstream:run_check', 'interval', hours=2,
                          id='werss_channel_check', max_instances=1, coalesce=True)
    if settings.werss_api_url and settings.werss_recovery_token_file and settings.werss_database_path:
        scheduler.add_job('app.body_recovery:run', 'interval', minutes=1,
                          id='werss_body_recovery', max_instances=1, coalesce=True)
    return scheduler
