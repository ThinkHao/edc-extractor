from datetime import datetime

import pytest

from edc_extractor.task_scheduler import scheduled_window, validate_cron_expression


def test_scheduled_window_applies_delay_and_five_minute_alignment():
    start, end = scheduled_window(
        datetime(2026, 5, 26, 12, 17, 31),
        time_window_minutes=60,
        delay_minutes=10,
    )

    assert end == datetime(2026, 5, 26, 12, 5)
    assert start == datetime(2026, 5, 26, 11, 5)


def test_validate_cron_expression_rejects_invalid_value():
    with pytest.raises(ValueError):
        validate_cron_expression("not a cron")
