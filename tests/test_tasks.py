"""Scheduler intervals must not reset at midnight."""
from datetime import timedelta
import pytest
from app.tasks import setup_scheduler


@pytest.mark.parametrize("hours", [1, 5, 24, 48])
def test_scheduler_uses_elapsed_interval(monkeypatch, hours):
    monkeypatch.setenv("SCHEDULED_SYNC_INTERVAL_HOURS", str(hours))
    monkeypatch.setenv("SCHEDULED_SYNC_TIMEZONE", "Asia/Shanghai")
    scheduler = setup_scheduler()
    job, = scheduler.get_jobs()
    assert str(scheduler.timezone) == "Asia/Shanghai"
    assert job.trigger.interval == timedelta(hours=hours)
