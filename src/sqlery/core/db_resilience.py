"""Database resilience utilities for SQLery workers.

Provides retry logic for transient DB errors and connection configuration
for SQLite (WAL mode, busy_timeout) and PostgreSQL (statement_timeout, lock_timeout).

Framework-agnostic: imports of Django and SQLAlchemy are guarded so this
module can be imported in either mode (or with neither installed, for
pure-import smoke tests).
"""

import functools
import logging
import re
import time

logger = logging.getLogger(__name__)

# --- Guarded Django imports ---------------------------------------------------
try:
    from django.db import (
        OperationalError as _django_OperationalError,
        connection as _django_connection,
        connections as _django_connections,
    )
    from django.db.utils import DatabaseError as _django_DatabaseError
except ImportError:  # pragma: no cover - exercised by standalone-mode tests
    _django_OperationalError = None
    _django_DatabaseError = None
    _django_connections = None
    _django_connection = None

# --- Guarded SQLAlchemy imports ----------------------------------------------
try:
    from sqlalchemy.exc import (
        OperationalError as _sa_OperationalError,
        DBAPIError as _sa_DBAPIError,
    )
except ImportError:  # pragma: no cover
    _sa_OperationalError = None
    _sa_DBAPIError = None

# Tuple of exception classes that retry_on_db_error treats as retryable.
# If neither Django nor SQLAlchemy is installed, fall back to (Exception,) so
# tests can still exercise the retry path; warn at module load time.
_RETRYABLE_EXC = tuple(
    e for e in (
        _django_OperationalError,
        _django_DatabaseError,
        _sa_OperationalError,
        _sa_DBAPIError,
    ) if e is not None
)
if not _RETRYABLE_EXC:
    logger.warning(
        "Neither django.db nor sqlalchemy.exc available; "
        "retry_on_db_error will fall back to catching Exception."
    )
    _RETRYABLE_EXC = (Exception,)

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


def _reset_connections() -> None:
    """Force reconnect for the active backend.

    In Django mode this calls ``django.db.connections.close_all()``. In
    standalone mode we attempt ``get_backend().reset_connections()`` if the
    backend implements it; otherwise this is a no-op.
    """
    if _django_connections is not None:
        try:
            _django_connections.close_all()
            return
        except Exception:
            pass

    # Standalone mode: defer the import to avoid circulars at module load.
    try:
        from sqlery.compat import get_backend
        backend = get_backend()
        reset = getattr(backend, "reset_connections", None)
        if callable(reset):
            reset()
        else:
            logger.debug("Standalone backend has no reset_connections(); skipping reconnect.")
    except Exception as exc:
        logger.debug("Could not reset standalone backend connections: %s", exc)


def retry_on_db_error(max_retries: int = 3, backoff_base: float = 0.1):
    """Decorator that retries a function on transient database errors.

    Catches OperationalError / DBAPIError (Django and/or SQLAlchemy variants,
    whichever are installed), checks if the error message matches a known
    transient pattern, and retries with exponential backoff. Forces a
    reconnect between retries.

    Args:
        max_retries: Maximum number of retry attempts (default 3).
        backoff_base: Base delay in seconds for exponential backoff (default 0.1).
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except _RETRYABLE_EXC as exc:
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
                        _reset_connections()
                    except Exception:
                        pass

                    time.sleep(delay)

            # Should not reach here, but just in case
            raise last_exc  # type: ignore[misc]

        return wrapper
    return decorator


def _get_setting(name, default):
    """Resolve a setting via the compat layer, falling back to the default.

    Routes through ``sqlery.compat.get_config`` (function-local import to
    avoid circular import at module load).
    """
    try:
        from sqlery.compat import get_config
        return get_config(name, default)
    except Exception:
        return default


def _resolve_active_connection_and_vendor():
    """Return (connection_or_None, vendor_or_None) for the active backend.

    In Django mode returns the live ``django.db.connection`` (which has a
    ``.vendor`` attribute and a ``.cursor()`` context manager). In standalone
    mode attempts to read ``get_backend().vendor`` for vendor-only routing;
    cursor-based configuration is skipped in standalone mode for now.
    """
    # Prefer Django connection if Django is installed and active.
    if _django_connection is not None:
        try:
            from sqlery.compat import is_django_mode
            if is_django_mode():
                return _django_connection, _django_connection.vendor
        except Exception:
            # Fall through to standalone path
            pass

    # Standalone mode: try to read vendor from backend, no usable cursor here.
    try:
        from sqlery.compat import get_backend
        backend = get_backend()
        vendor = getattr(backend, "vendor", None)
        return None, vendor
    except Exception:
        return None, None


def configure_connection_resilience(for_job_child: bool = False):
    """Configure database connection resilience settings.

    Called once on worker/daemon startup. Applies:
    - SQLite: WAL journal mode + busy_timeout pragma
    - PostgreSQL: statement_timeout + lock_timeout

    Args:
        for_job_child: When True (job child process), skips statement_timeout so
            user task queries are not killed by the DB. The job already has a
            SIGALRM-based timeout; statement_timeout would only interfere.

    Values come from settings via ``sqlery.compat.get_config`` with sensible
    defaults. In standalone mode where no usable cursor handle is available
    yet, this function logs a debug message and returns (no-op).
    """
    connection, vendor = _resolve_active_connection_and_vendor()
    if vendor is None:
        logger.debug("Could not determine DB vendor for resilience config; skipping.")
        return

    if connection is None:
        # Standalone mode with vendor known but no cursor handle: nothing to do.
        logger.debug(
            "Standalone backend reported vendor=%s but no cursor available; "
            "skipping resilience PRAGMA/SET.",
            vendor,
        )
        return

    if vendor == "sqlite":
        _configure_sqlite(connection, _get_setting)
    elif vendor == "postgresql":
        _configure_postgresql(connection, _get_setting, apply_statement_timeout=not for_job_child)


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
