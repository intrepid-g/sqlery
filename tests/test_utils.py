"""Tests for sqlery utilities."""

import pytest
from datetime import datetime, timezone as dt_timezone
from sqlery.utils import (
    calculate_next_run,
    validate_cron_expression,
    import_task,
)


def test_calculate_next_run():
    """Test calculating next run time from cron."""
    base_time = datetime(2024, 1, 1, 0, 0, 0, tzinfo=dt_timezone.utc)
    next_run = calculate_next_run("0 2 * * *", base_time)

    assert next_run.hour == 2
    assert next_run.minute == 0
    assert next_run.day == 1


def test_validate_cron_expression():
    """Test cron expression validation."""
    # Valid expression
    valid, error = validate_cron_expression("0 2 * * *")
    assert valid is True
    assert error is None

    # Invalid expression
    valid, error = validate_cron_expression("invalid")
    assert valid is False
    assert error is not None


def test_import_task_success():
    """Test importing a valid task."""
    # This will import pytest.main which is callable
    task = import_task("pytest.main")
    assert callable(task)


def test_import_task_not_found():
    """Test importing non-existent task."""
    with pytest.raises(ImportError):
        import_task("nonexistent.module.task")


def test_import_task_not_callable():
    """Test importing non-callable."""
    with pytest.raises(ImportError):
        import_task("pytest.__version__")  # This is a string, not callable
