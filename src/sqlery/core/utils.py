"""Framework-agnostic utility functions for sqlery.

Consolidated from django_sqlery/utils.py and rate_limit_utils.py.
These functions have zero framework dependencies.
"""

import inspect
import re
from datetime import datetime, timedelta, timezone as dt_timezone
from importlib import import_module

from sqlery.crontab import next_cron_occurrence, parse_cron_string, InvalidExpression


# ===== Result recording utilities =====

def is_ttl_expired(job, now: datetime) -> bool:
    """True if a queued job's TTL has elapsed. Mirrors backend.get_expired_ttl_jobs.

    ttl=0 counts as expired (never truthiness-check ttl). Boundary is a strict
    `<` so this exactly mirrors get_expired_ttl_jobs — no limbo between "not
    expired yet" and "expired but still claimable".
    """
    ttl = getattr(job, "ttl", None)
    if ttl is None:
        return False
    created = job.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=dt_timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt_timezone.utc)
    return created + timedelta(seconds=ttl) < now


def reject_unawaited_coroutine(output) -> None:
    """Guard against storing an unawaited coroutine/awaitable as a job result.

    A caller that forgets to await an async task's return value ends up with
    the coroutine object itself as `output` -- the job then gets recorded as
    successful even though its body never ran. Every result-recording path
    (`QueuedJob.mark_success` in both integration modes) calls this first, so
    the bug class is structurally caught regardless of which executor call
    site produced the unawaited value.

    Raises:
        TypeError: If `output` is a coroutine or other awaitable.
    """
    if inspect.isawaitable(output):
        raise TypeError(
            f"Job result is an unawaited {type(output).__name__!r} -- the task "
            "body never ran. Await/run the coroutine before recording the result."
        )


# ===== Cron utilities =====

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


# ===== Task import =====

def import_task(task_path):
    """Import a callable from a string path.

    Args:
        task_path: String like "myapp.tasks.my_function"

    Returns:
        callable: The imported function

    Raises:
        ImportError: If task cannot be imported
    """
    try:
        module_path, function_name = task_path.rsplit(".", 1)
    except ValueError:
        raise ImportError(f"Cannot import task '{task_path}': no module separator")

    # SEC-04: enforce ALLOWED_TASK_MODULES before importlib resolves the module.
    from .security import check_task_module_allowed
    from ..compat import get_config
    check_task_module_allowed(module_path, get_config("ALLOWED_TASK_MODULES", None))

    try:
        module = import_module(module_path)
        task_func = getattr(module, function_name)

        if not callable(task_func):
            raise ImportError(f"{task_path} is not callable")

        return task_func
    except (ImportError, AttributeError) as e:
        raise ImportError(f"Cannot import task '{task_path}': {e}")


# ===== Rate limit utilities =====

def parse_rate_limit(rate_limit_str: str) -> tuple[int, timedelta]:
    """Parse rate limit string into (count, time_window).

    Rate limit format: "{count}/{unit}" where:
    - count: Number of allowed executions
    - unit: Time unit - s (second), m (minute), h (hour), or custom like 10s

    Args:
        rate_limit_str: Rate limit string (e.g., "60/m", "100/s", "1/10s")

    Returns:
        Tuple of (count, time_window as timedelta)

    Raises:
        ValueError: If rate limit string is invalid

    Examples:
        >>> parse_rate_limit("60/m")
        (60, timedelta(minutes=1))

        >>> parse_rate_limit("100/s")
        (100, timedelta(seconds=1))

        >>> parse_rate_limit("1/10s")
        (1, timedelta(seconds=10))

        >>> parse_rate_limit("5000/h")
        (5000, timedelta(hours=1))
    """
    pattern = r"^(\d+)/(\d+)?([smh])$"
    match = re.match(pattern, rate_limit_str)

    if not match:
        raise ValueError(
            f"Invalid rate limit format: '{rate_limit_str}'. "
            f"Expected format: '{{count}}/{{unit}}' where unit is s, m, h, or like 10s, 30m"
        )

    count = int(match.group(1))
    multiplier = int(match.group(2)) if match.group(2) else 1
    unit = match.group(3)

    if count <= 0:
        raise ValueError(
            f"Rate limit count must be positive, got {count}. "
            f"Example: '60/m' allows 60 requests per minute."
        )
    if multiplier <= 0:
        raise ValueError(
            f"Time multiplier must be positive, got {multiplier}. "
            f"Example: '1/10s' means 1 request per 10 seconds."
        )

    if unit == "s":
        time_window = timedelta(seconds=multiplier)
    elif unit == "m":
        time_window = timedelta(minutes=multiplier)
    elif unit == "h":
        time_window = timedelta(hours=multiplier)
    else:
        raise ValueError(f"Unknown time unit: '{unit}'. Use 's', 'm', or 'h'")

    return count, time_window


def calculate_rate_limit_seconds(rate_limit_str: str) -> tuple[int, float]:
    """Calculate rate limit as (count, seconds).

    Args:
        rate_limit_str: Rate limit string (e.g., "60/m")

    Returns:
        Tuple of (count, seconds in time window)

    Examples:
        >>> calculate_rate_limit_seconds("60/m")
        (60, 60.0)

        >>> calculate_rate_limit_seconds("100/s")
        (100, 1.0)

        >>> calculate_rate_limit_seconds("1/10s")
        (1, 10.0)
    """
    count, time_window = parse_rate_limit(rate_limit_str)
    return count, time_window.total_seconds()


def get_rate_limit_description(rate_limit_str: str) -> str:
    """Get human-readable description of rate limit.

    Args:
        rate_limit_str: Rate limit string

    Returns:
        Human-readable description

    Examples:
        >>> get_rate_limit_description("60/m")
        '60 requests per minute (1.00 per second)'

        >>> get_rate_limit_description("100/s")
        '100 requests per second'

        >>> get_rate_limit_description("1/10s")
        '1 requests per 10 seconds (0.10 per second)'
    """
    count, seconds = calculate_rate_limit_seconds(rate_limit_str)

    if seconds == 1:
        desc = f"{count} requests per second"
    elif seconds == 60:
        desc = f"{count} requests per minute"
        rate_per_second = count / 60
        desc += f" ({rate_per_second:.2f} per second)"
    elif seconds == 3600:
        desc = f"{count} requests per hour"
        rate_per_second = count / 3600
        desc += f" ({rate_per_second:.4f} per second)"
    else:
        desc = f"{count} requests per {seconds:.0f} seconds"
        rate_per_second = count / seconds
        desc += f" ({rate_per_second:.2f} per second)"

    return desc
