from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from app.config import get_settings
from app.sync_service import month_batches


@pytest.mark.parametrize("name,value", [
    ("SCHEDULED_SYNC_INTERVAL_HOURS", "0"),
    ("SCHEDULED_SYNC_INTERVAL_HOURS", "oops"),
    ("SCHEDULED_SYNC_TIMEZONE", "Mars/Unknown"),
    ("SCHEDULER_ENABLED", "maybe"),
])
def test_invalid_configuration_names_the_setting(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        get_settings()


@pytest.mark.parametrize("year,month,total,size", [
    (2026, 1, 5, 2), (2026, 12, 14, 3), (2024, 3, 3, 2),
    (2024, 2, 1, 3), (2026, 1, 0, 3),
])
def test_month_batches_cover_exact_contiguous_months(year, month, total, size):
    with patch("app.sync_service.datetime") as clock:
        clock.now.return_value = datetime(year, month, 15)
        clock.side_effect = datetime
        batches = month_batches(total, size)
    assert sum(count for _, _, count in batches) == total
    for i, (start, end, count) in enumerate(batches):
        assert (end.year - start.year) * 12 + end.month - start.month + 1 == count
        assert start.day == 1
        assert (end + timedelta(seconds=1)).day == 1
        if i:
            assert end + timedelta(seconds=1) == batches[i - 1][0]


def test_january_two_month_batch_starts_in_december():
    with patch("app.sync_service.datetime") as clock:
        clock.now.return_value = datetime(2026, 1, 15)
        clock.side_effect = datetime
        assert month_batches(2, 2) == [
            (datetime(2025, 12, 1), datetime(2026, 1, 31, 23, 59, 59), 2)
        ]


@pytest.mark.parametrize("total,size", [(-1, 2), (1, 0), (1, -1)])
def test_invalid_batch_sizes_do_not_loop_forever(total, size):
    with pytest.raises(ValueError):
        month_batches(total, size)
