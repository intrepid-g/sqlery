"""Database resilience utilities for SQLery workers.

Provides retry logic for transient DB errors and connection configuration
for SQLite (WAL mode, busy_timeout) and PostgreSQL (statement_timeout, lock_timeout).
"""

import functools
import logging
import re
import time

from django.db import OperationalError, connection, connections
from django.db.utils import DatabaseError

logger = logging.getLogger(__name__)

# Transient error patterns that are safe to retry
_TRANSIENT_PATTERNS = [
    # SQLite
    re.compile(r"database is locked", re.IGNORECASE),
    re.compile(r"disk I/O error", re.IGNORECASE),
    # PostgreSQL
    re.compile(r"server closed the connection", re.IGNORECASE),
    re.compile(r"connection already closed", re.IGNORECASE),
    re.compile(r"deadlock detected", re.IGNORECASE),
    re.compile(r"could not serialize access", re.IGNORECASE),
    re.compile(r"canceling statement due to statement timeout", re.IGNORECASE),
    re.compile(r"SSL connection has been closed unexpectedly", re.IGNORECASE),
    re.compile(r"could not connect to server", re.IGNORECASE),
]


def _is_transient_error(exc: Exception) -> bool:
    """Check if an exception matches a known transient DB error pattern."""
    msg = str(exc)
    return any(pattern.search(msg) for pattern in _TRANSIENT_PATTERNS)


def retry_on_db_error(max_retries: int = 3, backoff_base: float = 0.1):
    """Decorator that retries a function on transient database errors.

    Catches OperationalError and DatabaseError, checks if the error is
    transient, and retries with exponential backoff. Calls
    connections.close_all() between retries to force reconnection.

    Args:
        max_retries: Maximum number of retry attempts (default 3).
        backoff_base: Base delay in seconds for exponential backoff (default 0.1).
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # from django.db import OperationalError  # moved to top-level
            # from django.db.utils import DatabaseError  # moved to top-level

            last_exc = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except (OperationalError, DatabaseError) as exc:
                    last_exc = exc
                    if attempt >= max_retries or not _is_transient_error(exc):
                        raise

                    delay = backoff_base * (2 ** attempt)
                    logger.warning(
                        "Transient DB error in %s (attempt %d/%d), retrying in %.2fs: %s",
                        func.__qualname__,
                        attempt + 1,
                        max_retries + 1,
                        delay,
                        exc,
                    )

                    # Force reconnect before retry
                    try:
                        # from django.db import connections  # moved to top-level
                        connections.close_all()
                    except Exception:
                        pass

                    time.sleep(delay)

            # Should not reach here, but just in case
            raise last_exc  # type: ignore[misc]

        return wrapper
    return decorator


def configure_connection_resilience(for_job_child: bool = False):
    """Configure database connection resilience settings.

    Called once on worker/daemon startup. Applies:
    - SQLite: WAL journal mode + busy_timeout pragma
    - PostgreSQL: statement_timeout + lock_timeout

    Args:
        for_job_child: When True (job child process), skips statement_timeout so
            user task queries are not killed by the DB. The job already has a
            SIGALRM-based timeout; statement_timeout would only interfere.

    Values come from DJANGO_SQL_JOBS settings with sensible defaults.
    """
    # from django.db import connection  # moved to top-level

    try:
        vendor = connection.vendor
    except Exception:
        logger.warning("Could not determine DB vendor for resilience config")
        return

    # Lazy import settings to avoid circular imports
    try:
        from sqlery.django_sqlery.settings import get_setting
    except ImportError:
        logger.debug("django_sqlery.settings not available, skipping resilience config")
        return

    if vendor == "sqlite":
        _configure_sqlite(connection, get_setting)
    elif vendor == "postgresql":
        _configure_postgresql(connection, get_setting, apply_statement_timeout=not for_job_child)


def _configure_sqlite(connection, get_setting):
    """Apply SQLite-specific resilience settings."""
    wal_mode = get_setting("SQLITE_WAL_MODE", True)
    busy_timeout_ms = get_setting("SQLITE_BUSY_TIMEOUT_MS", 5000)

    try:
        with connection.cursor() as cursor:
            if wal_mode:
                cursor.execute("PRAGMA journal_mode=WAL")
                result = cursor.fetchone()
                logger.info("SQLite journal_mode set to: %s", result[0] if result else "unknown")

            if busy_timeout_ms:
                cursor.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
                logger.info("SQLite busy_timeout set to: %dms", busy_timeout_ms)
    except Exception as exc:
        logger.warning("Failed to configure SQLite resilience: %s", exc)


def _configure_postgresql(connection, get_setting, apply_statement_timeout: bool = True):
    """Apply PostgreSQL-specific resilience settings."""
    statement_timeout_ms = get_setting("PG_STATEMENT_TIMEOUT_MS", 30000)
    lock_timeout_ms = get_setting("PG_LOCK_TIMEOUT_MS", 10000)

    try:
        with connection.cursor() as cursor:
            if apply_statement_timeout and statement_timeout_ms:
                cursor.execute(f"SET statement_timeout = '{int(statement_timeout_ms)}'")
                logger.info("PostgreSQL statement_timeout set to: %dms", statement_timeout_ms)

            if lock_timeout_ms:
                cursor.execute(f"SET lock_timeout = '{int(lock_timeout_ms)}'")
                logger.info("PostgreSQL lock_timeout set to: %dms", lock_timeout_ms)
    except Exception as exc:
        logger.warning("Failed to configure PostgreSQL resilience: %s", exc)
