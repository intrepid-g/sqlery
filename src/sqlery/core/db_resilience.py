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
        if vendor is None:
            # The SQLAlchemy backend has no ``vendor``; ask its engine.
            from sqlery.fastapi_sqlery.database import get_engine

            vendor = get_engine().dialect.name
        return None, vendor
    except Exception:
        return None, None


# Session settings the standalone engine applies to every NEW pooled PostgreSQL
# connection (a plain ``SET`` is lost when a pooled connection is recycled).
_standalone_session_settings: dict[str, int] = {}


def _pg_session_settings(get_setting, for_job_child: bool) -> dict[str, int]:
    """PostgreSQL session GUCs (name -> ms) sqlery applies to its own sessions.

    ``idle_in_transaction_session_timeout`` is the xmin-horizon guard: a session
    left idle inside a transaction pins the horizon and stops VACUUM. Like
    ``statement_timeout`` it is skipped for job children, which run user code. 0
    (or unset/invalid) disables a setting.
    """
    wanted = {"lock_timeout": ("PG_LOCK_TIMEOUT_MS", 10000)}
    if not for_job_child:
        wanted["statement_timeout"] = ("PG_STATEMENT_TIMEOUT_MS", 30000)
        wanted["idle_in_transaction_session_timeout"] = (
            "PG_IDLE_IN_TRANSACTION_TIMEOUT_MS",
            60000,
        )
    out = {}
    for guc, (key, default) in wanted.items():
        try:
            value = int(get_setting(key, default))
        except (TypeError, ValueError):
            value = default
        if value > 0:
            out[guc] = value
    return out


def _install_standalone_pg_settings(settings: dict[str, int]) -> None:
    """Make the standalone SQLAlchemy engine apply ``settings`` to new connections."""
    from sqlalchemy import event

    from sqlery.fastapi_sqlery.database import get_engine

    engine = get_engine()
    if engine.dialect.name != "postgresql":
        return
    _standalone_session_settings.clear()
    _standalone_session_settings.update(settings)
    if not getattr(engine, "_sqlery_pg_guard_installed", False):

        @event.listens_for(engine, "connect")
        def _apply(dbapi_conn, _record):
            try:
                with dbapi_conn.cursor() as cur:
                    for guc, ms in _standalone_session_settings.items():
                        cur.execute(f"SET {guc} = '{int(ms)}'")
                dbapi_conn.commit()
            except Exception as exc:
                logger.warning("Failed to apply PostgreSQL session settings: %s", exc)

        engine._sqlery_pg_guard_installed = True
    # Drop idle pooled connections so they are re-created with the settings.
    engine.dispose()
    logger.info("PostgreSQL session settings for standalone engine: %s", settings)


def configure_connection_resilience(for_job_child: bool = False):
    """Configure database connection resilience settings.

    Called once on worker/daemon startup. Applies:
    - SQLite: WAL journal mode + busy_timeout pragma
    - PostgreSQL: statement_timeout + lock_timeout + idle_in_transaction_session_timeout

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

    if connection is None and vendor == "postgresql":
        try:
            _install_standalone_pg_settings(_pg_session_settings(_get_setting, for_job_child))
        except Exception as exc:
            logger.warning("Failed to configure standalone PostgreSQL resilience: %s", exc)
        return

    if for_job_child and _is_configured(connection):
        return  # #172: a job child's session keeps its settings; do not SET them for every job

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
        _configure_postgresql(connection, _get_setting, for_job_child=for_job_child)
    if for_job_child:
        _mark_configured(connection)  # the cursor above opened it, if it was not open


# The raw DB connection a job child configured last (#172). Held strongly (sqlite3 connections
# cannot be weakly referenced); a closed one is replaced by the next, so at most one lingers.
_job_child_configured = None


def _raw_connection(connection):
    return getattr(connection, "connection", None)


def _is_configured(connection) -> bool:
    """True if the raw connection under ``connection`` was configured by the last job child call."""
    raw = _raw_connection(connection)
    return raw is not None and raw is _job_child_configured


def _mark_configured(connection) -> None:
    global _job_child_configured
    _job_child_configured = _raw_connection(connection)


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


def _configure_postgresql(connection, get_setting, for_job_child: bool = False):
    """Apply PostgreSQL-specific resilience settings to the current connection."""
    try:
        with connection.cursor() as cursor:
            for guc, ms in _pg_session_settings(get_setting, for_job_child).items():
                cursor.execute(f"SET {guc} = '{int(ms)}'")
                logger.info("PostgreSQL %s set to: %dms", guc, ms)
    except Exception as exc:
        logger.warning("Failed to configure PostgreSQL resilience: %s", exc)


# --- Connection loss (#161) ---------------------------------------------------
# A lost connection is discarded entirely and reopened with exponential backoff and
# jitter. Workers never re-run a job because its connection failed: the supervisor
# (the worker parent) persists an outcome the child could not write, and stops
# claiming while the database stays unreachable.

try:
    import psycopg as _psycopg
except ImportError:  # pragma: no cover - SQLite-only installs
    _psycopg = None

try:
    from django.db.utils import InterfaceError as _django_InterfaceError
except ImportError:  # pragma: no cover
    _django_InterfaceError = None

# SQLSTATE class 08 (connection exception) and the server-shutdown codes of class 57.
_CONNECTION_LOST_SQLSTATES = ("57P01", "57P02", "57P03")

_CONNECTION_LOST_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"server closed the connection",
        r"connection already closed",
        r"the connection is (lost|closed)",
        r"SSL connection has been closed unexpectedly",
        r"could not connect to server",
        r"connection to server .* failed",
        r"connection refused",
        r"terminating connection due to administrator command",
        r"consuming input failed",
        r"no connection to the server",
        r"connection reset by peer",
        r"broken pipe",
    )
]


def is_connection_lost(exc: BaseException | None) -> bool:
    """Whether ``exc`` (or an exception it was raised from) means the DB connection is gone.

    Statement-level errors (lock or statement timeouts, deadlocks, constraint violations,
    SQLite's "database is locked") are not connection loss: the connection still works.
    """
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if getattr(exc, "connection_invalidated", False):  # SQLAlchemy DBAPIError
            return True
        if _django_InterfaceError is not None and isinstance(exc, _django_InterfaceError):
            return True
        if _psycopg is not None and isinstance(exc, _psycopg.InterfaceError):
            return True
        if _psycopg is not None and isinstance(exc, _psycopg.OperationalError):
            sqlstate = getattr(exc, "sqlstate", None)
            if sqlstate is None or sqlstate.startswith("08") or (
                sqlstate in _CONNECTION_LOST_SQLSTATES
            ):
                return True
        if any(pattern.search(str(exc)) for pattern in _CONNECTION_LOST_PATTERNS):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def reconnect_delay(attempt: int, base: float = 0.5, cap: float = 30.0, rng=None) -> float:
    """Seconds before reconnect ``attempt`` (0-based): exponential, capped, full jitter."""
    import random

    ceiling = min(float(cap), float(base) * (2 ** min(attempt, 32)))
    return (rng or random.random)() * ceiling


def reconnect_backoff_settings() -> tuple[float, float]:
    """``(DB_RECONNECT_BACKOFF_BASE, DB_RECONNECT_BACKOFF_MAX)`` in seconds."""
    try:
        base = float(_get_setting("DB_RECONNECT_BACKOFF_BASE", 0.5))
        cap = float(_get_setting("DB_RECONNECT_BACKOFF_MAX", 30.0))
    except (TypeError, ValueError):
        return 0.5, 30.0
    return max(base, 0.001), max(cap, 0.001)


def discard_connections() -> None:
    """Drop every DB connection of this process without reusing any of it.

    Django connections are closed (a close that fails on a dead socket is ignored);
    the SQLAlchemy pool is disposed, so the next query opens a new connection.
    """
    if _django_connections is not None:
        try:
            from django.conf import settings

            configured = settings.configured
        except Exception:
            configured = False
        if configured:
            for conn in _django_connections.all(initialized_only=True):
                try:
                    conn.close()
                except Exception:
                    conn.connection = None
    try:
        from sqlery.fastapi_sqlery import database

        if database._engine is not None:
            database._engine.dispose()
    except ImportError:
        pass
    except Exception as exc:
        logger.debug("Disposing the SQLAlchemy pool failed: %s", exc)


def probe_database() -> None:
    """Open a connection and run ``SELECT 1``; raises when the database is unreachable."""
    try:
        from sqlery.fastapi_sqlery import database

        engine = database._engine
    except ImportError:
        engine = None
    if engine is not None:
        from sqlalchemy import text

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return
    if _django_connection is not None:
        with _django_connection.cursor() as cursor:
            cursor.execute("SELECT 1")
