"""FastAPI 应用入口。"""

from __future__ import annotations

import logging
from pathlib import Path
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.database import init_db
from app.news_data import count_news_records
from app.routers import news_api, sync_routes, web, learning, knowledge, onboarding, review, practice, material_import, upstream, body_recovery, search
from app.sync_service import (
    reset_stale_sync_state,
    start_background_sync,
)
from app.tasks import setup_scheduler
from app.routers import curation, events, reports, news_quality, material_sources, material_coverage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_db()
    from app.knowledge import rebuild_links
    rebuild_links()
    from app.news_knowledge import rebuild_news_links
    rebuild_news_links()
    from app.events import rebuild_events
    rebuild_events()
    from app.material_status import recover_import
    recover_import()
    from app.body_recovery import recover_interrupted
    recover_interrupted()
    reset_stale_sync_state()
    settings = get_settings()
    scheduler = setup_scheduler() if settings.scheduler_enabled else None
    if scheduler is not None:
        scheduler.start()
        logger.info("定时任务调度器已启动")
        from app.upstream import configured, status, start_check
        if configured():
            current_check = status()
            if not current_check.get('fresh') and not current_check['retry_after_seconds']:
                start_check()

    try:
        db_is_empty = count_news_records() == 0
        should_bootstrap = db_is_empty and settings.bootstrap_recent_news_on_startup

        if should_bootstrap:
            scope = "启动近期新闻初始化"
            started = start_background_sync(scope, months=1, max_pages=2, max_items=40)
            if started:
                logger.info("Startup recent news sync started in background; historical backfill is available manually.")
            else:
                logger.info("Startup recent news sync skipped because another sync task is running.")
        elif settings.auto_sync_on_startup:
            scope = "启动快速同步"
            started = start_background_sync(scope, months=1, max_pages=40, max_items=240)
            if started:
                logger.info("Startup quick sync started in background.")
            else:
                logger.info("Startup quick sync skipped because another sync task is running.")
        else:
            logger.info("Startup auto sync is disabled; serving cached news.")
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title="时政资料库 / Political News",
        description="中国政府网时政聚合、按月归档、年份筛选与关键词搜索。",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.expose_api_docs else None,
        redoc_url="/redoc" if settings.expose_api_docs else None,
        openapi_url="/openapi.json" if settings.expose_api_docs else None,
        openapi_tags=[
            {"name": "页面", "description": "HTML 阅读界面"},
            {"name": "新闻 API", "description": "JSON 接口（支持年份、关键词与时间维度）"},
            {"name": "同步", "description": "数据抓取与后台任务"},
        ],
    )
    application.include_router(web.router)
    application.include_router(news_api.router)
    application.include_router(sync_routes.router)
    application.include_router(learning.router)
    application.include_router(knowledge.router)
    application.include_router(onboarding.router)
    application.include_router(review.router)
    application.include_router(practice.router)
    application.include_router(material_import.router)
    application.include_router(upstream.router)
    application.include_router(body_recovery.router)
    application.include_router(search.router)
    application.include_router(curation.router)
    application.include_router(events.router)
    application.include_router(reports.router)
    application.include_router(news_quality.router)
    application.include_router(material_sources.router)
    application.include_router(material_coverage.router)
    application.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
    return application


app = create_app()
