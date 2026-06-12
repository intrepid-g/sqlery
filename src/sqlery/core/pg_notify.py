"""PG LISTEN/NOTIFY helpers for sqlery Phase 18 opt-in dispatch."""

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Phase 18: guard-import Django transaction at module level.
# Set _django_transaction = None if Django is not installed.
try:
    from django.db import transaction as _django_transaction
except ImportError:
    _django_transaction = None  # type: ignore[assignment]

# Phase 18 (IN-01): guard-import Django connection at module level so the
# inline `from django.db import connection` inside functions can be removed
# (project convention: imports at top of file, ORM imports guarded in core/).
try:
    from django.db import connection as _django_connection
except ImportError:
    _django_connection = None  # type: ignore[assignment]

# Phase 18: guard-import SQLAlchemy text() at module level.
# Set _sa_text = None if SQLAlchemy is not installed.
try:
    from sqlalchemy import text as _sa_text
except ImportError:
    _sa_text = None  # type: ignore[assignment]

__all__ = ["sanitize_queue_name_to_channel", "notify_queue_django", "notify_queue_sqlalchemy"]


def sanitize_queue_name_to_channel(queue_name: str) -> str:
    """Sanitize a queue name into a safe PostgreSQL NOTIFY channel identifier.

    Replaces any character outside [a-zA-Z0-9_] with underscore, prepends
    'sqlery_job_', and truncates the result to 63 characters (the PostgreSQL
    identifier length limit).

    Security: the re.sub guarantee ensures the channel is composed only of
    alphanumeric chars and underscores before being passed to pg_notify via a
    parameterized query — no injection risk even from untrusted queue names.

    Args:
        queue_name: Raw queue name from application code.

    Returns:
        A safe channel string matching pattern sqlery_job_<sanitized_queue>.

    Raises:
        ValueError: If queue_name is empty or whitespace-only.
    """
    if not queue_name or not queue_name.strip():
        raise ValueError("queue_name must be non-empty")
    # Old: truncate AFTER prefixing — two long distinct queue names sharing the
    # first 52 chars collapsed to the same channel (WR-02). Truncate the
    # sanitized suffix FIRST so the visible suffix is cut, never the prefix.
    # sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", queue_name)
    # channel = f"sqlery_job_{sanitized}"
    # return channel[:63]
    _PREFIX = "sqlery_job_"
    _max_suffix = 63 - len(_PREFIX)  # 52 chars of sanitized queue suffix
    sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", queue_name)[:_max_suffix]
    channel = f"{_PREFIX}{sanitized}"
    return channel


def _fire_django_notify(channel: str) -> None:
    """Execute pg_notify on the active Django DB connection.

    Called inside transaction.on_commit so it fires after the INSERT commits.
    Wrapped in try/except so a NOTIFY failure never crashes the enqueue call.

    Args:
        channel: Pre-sanitized PG channel name.
    """
    try:
        # Old: from django.db import connection  # noqa: PLC0415 — guarded import
        # Phase 18 (IN-01): use the module-level guarded _django_connection.
        if _django_connection is None:
            return
        with _django_connection.cursor() as cur:
            cur.execute("SELECT pg_notify(%s, '')", [channel])
    except Exception:
        logger.warning("pg_notify fire failed for channel %r", channel, exc_info=True)


def notify_queue_django(queue_name: str) -> None:
    """Emit pg_notify after Django transaction commits. No-op on SQLite or non-PG.

    Schedules a pg_notify('sqlery_job_<sanitized_queue>', '') via
    transaction.on_commit so the notification fires only after the job INSERT
    has been committed to the database.

    Args:
        queue_name: Queue name to notify on.
    """
    if _django_transaction is None:
        return
    try:
        # Old: from django.db import connection  # noqa: PLC0415 — guarded import
        # Phase 18 (IN-01): use the module-level guarded _django_connection.
        if _django_connection is None:
            return
        if _django_connection.vendor != "postgresql":
            return
    except Exception:
        return
    channel = sanitize_queue_name_to_channel(queue_name)
    _django_transaction.on_commit(lambda: _fire_django_notify(channel))


def notify_queue_sqlalchemy(queue_name: str, engine: Any) -> None:
    """Emit pg_notify on a dedicated AUTOCOMMIT connection. No-op on SQLite.

    CR-01 fix: the previous implementation called ``session.execute(SELECT
    pg_notify(...))`` AFTER ``session.commit()``. SQLAlchemy 2's autobegin
    semantics opened a fresh implicit transaction for that execute, which
    ``get_session()``'s ``finally: session.close()`` then ROLLED BACK — so
    PostgreSQL never dispatched the NOTIFY (it only dispatches on COMMIT).
    Firing on a separate AUTOCOMMIT connection means there is no transaction
    to roll back; the notification is delivered immediately.

    Any NOTIFY failure is caught and logged — never crashes the enqueue call.

    Args:
        queue_name: Queue name to notify on.
        engine: SQLAlchemy Engine (e.g. from get_engine()).
    """
    # Old: fired through the Session in a rolled-back SA2 implicit transaction
    #      — notification was silently suppressed (CR-01).
    # def notify_queue_sqlalchemy(queue_name: str, session: Any) -> None:
    #     ...
    #     session.execute(_sa_text("SELECT pg_notify(:ch, '')"), {"ch": channel})
    if _sa_text is None or engine is None:
        return
    try:
        dialect_name = engine.dialect.name
    except Exception:
        return
    if dialect_name != "postgresql":
        return
    channel = sanitize_queue_name_to_channel(queue_name)
    try:
        with engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as conn:
            conn.execute(_sa_text("SELECT pg_notify(:ch, '')"), {"ch": channel})
    except Exception:
        logger.warning("pg_notify fire failed for channel %r", channel, exc_info=True)
