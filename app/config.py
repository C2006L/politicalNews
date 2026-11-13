"""应用配置（环境变量）。"""

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _truthy_env(name: str, default: str = "0") -> bool:
    value = os.getenv(name, default).strip().lower()
    if value not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
        raise ValueError(f"{name} must be a boolean (0/1, true/false, yes/no, on/off)")
    return value in {"1", "true", "yes", "on"}


def _interval_hours() -> int:
    try:
        value = int(os.getenv("SCHEDULED_SYNC_INTERVAL_HOURS", "1"))
        if value < 1:
            raise ValueError
        return value
    except ValueError as exc:
        raise ValueError("SCHEDULED_SYNC_INTERVAL_HOURS must be a positive integer") from exc


def _timezone() -> str:
    value = os.getenv("SCHEDULED_SYNC_TIMEZONE", "Asia/Shanghai").strip()
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Invalid SCHEDULED_SYNC_TIMEZONE: {value!r}") from exc
    return value


def _normalize_base_path(value: str) -> str:
    """规范化 BASE_PATH：去首尾空白和末尾斜杠，空/根返回空串。"""
    value = (value or "").strip().rstrip("/")
    return value


@dataclass(frozen=True)
class Settings:
    auto_sync_on_startup: bool
    bootstrap_recent_news_on_startup: bool
    sync_admin_token: str
    scheduled_sync_interval_hours: int
    scheduled_sync_timezone: str
    expose_api_docs: bool
    base_path: str
    scheduler_enabled: bool = True
    werss_database_path: str = ""


def get_settings() -> Settings:
    return Settings(
        auto_sync_on_startup=_truthy_env("AUTO_SYNC_ON_STARTUP", "0"),
        bootstrap_recent_news_on_startup=_truthy_env("BOOTSTRAP_RECENT_NEWS_ON_STARTUP", "1"),
        sync_admin_token=os.getenv("SYNC_ADMIN_TOKEN", "").strip(),
        scheduled_sync_interval_hours=_interval_hours(),
        scheduled_sync_timezone=_timezone(),
        expose_api_docs=_truthy_env("EXPOSE_API_DOCS", "1"),
        base_path=_normalize_base_path(os.getenv("BASE_PATH", "")),
        scheduler_enabled=_truthy_env("SCHEDULER_ENABLED", "1"),
        werss_database_path=os.getenv("WERSS_DATABASE_PATH", "").strip(),
    )
