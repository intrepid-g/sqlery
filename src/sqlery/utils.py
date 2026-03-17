"""Utility functions for sqlery."""

from datetime import datetime, timezone as dt_timezone
from .crontab import next_cron_occurrence, parse_cron_string, InvalidExpression


def calculate_next_run(cron_expression, base_time=None):
    """Calculate next run time from cron expression.

    Args:
        cron_expression: Cron string like "0 2 * * *"
        base_time: Base datetime (defaults to now UTC)

    Returns:
        datetime: Next run time in UTC
    """
    if base_time is None:
        base_time = datetime.now(dt_timezone.utc)

    # Ensure base_time is timezone-aware
    if base_time.tzinfo is None:
        base_time = base_time.replace(tzinfo=dt_timezone.utc)

    next_run = next_cron_occurrence(cron_expression, base_time)

    # Ensure UTC
    if next_run.tzinfo is None:
        next_run = next_run.replace(tzinfo=dt_timezone.utc)

    return next_run


def validate_cron_expression(cron_expression):
    """Validate cron expression.

    Returns:
        tuple: (is_valid, error_message)
    """
    try:
        parse_cron_string(cron_expression)
        return True, None
    except InvalidExpression as e:
        return False, str(e)


def import_task(task_path):
    """Import a callable from a string path.

    Args:
        task_path: String like "myapp.tasks.my_function"

    Returns:
        callable: The imported function

    Raises:
        ImportError: If task cannot be imported
    """
    from importlib import import_module

    try:
        module_path, function_name = task_path.rsplit(".", 1)
        module = import_module(module_path)
        task_func = getattr(module, function_name)

        if not callable(task_func):
            raise ImportError(f"{task_path} is not callable")

        return task_func
    except (ValueError, ImportError, AttributeError) as e:
        raise ImportError(f"Cannot import task '{task_path}': {e}")
