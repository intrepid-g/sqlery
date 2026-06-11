"""Framework-agnostic PostgreSQL partition maintenance for sqlery.

Functions in this module accept a raw psycopg cursor only.  No Django or
SQLAlchemy symbols are imported at module level — the module is usable from
any execution context (daemon, management command, CLI, tests).

Advisory-lock key derivation:
  ADVISORY_LOCK_ENSURE  = int.from_bytes(b"SQLEPART", "big")  # 'SQLEPART' as int8
  ADVISORY_LOCK_RECLAIM = int.from_bytes(b"SQLERCLA", "big")  # 'SQLERCLA' as int8
Both values fit comfortably in PostgreSQL's signed int8 range.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import psycopg
from psycopg import sql as pgsql

__all__ = ["ensure_future_partitions", "reclaim_drained_partitions", "check_default_partition"]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Advisory-lock constants (stable int64s — documented above)
# ---------------------------------------------------------------------------

# Derived from ASCII bytes of the 8-char tags below; both fit in signed int8.
ADVISORY_LOCK_ENSURE: int = int.from_bytes(b"SQLEPART", "big")   # partition ensure DDL lock
ADVISORY_LOCK_RECLAIM: int = int.from_bytes(b"SQLERCLA", "big")  # partition reclaim DDL lock

# Regex to extract the TO ('...') timestamp from a partition bound expression.
_BOUND_TO_RE = re.compile(r"TO \('([^']+)'\)")

# Regex to detect sub-daily interval strings (hours or minutes).
_SUB_DAILY_RE = re.compile(r'\b(hour|minute)s?\b', re.IGNORECASE)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _is_sub_daily_interval(interval_str: str) -> bool:
    """Return True if *interval_str* represents a sub-daily interval (hours or minutes).

    Used to decide the partition name suffix precision: daily-or-coarser intervals
    use ``%Y%m%d``; sub-daily intervals use ``%Y%m%d_%H%M`` to avoid name collisions
    when multiple partitions fall on the same date.
    """
    return bool(_SUB_DAILY_RE.search(interval_str))


def _list_partitions(cur, table: str) -> list[tuple[str, datetime | None]]:
    """Return (name, upper_bound) for every child partition of *table*.

    Queries pg_inherits + pg_get_expr to read the partition boundary
    expressions directly from the PostgreSQL catalog.

    Args:
        cur: psycopg cursor.
        table: Unquoted parent table name (e.g. "sqlery_queued_job").

    Returns:
        List of (partition_name, upper_bound) tuples.  upper_bound is None
        for the DEFAULT partition (or any expression that does not contain the
        expected ``TO ('...')`` pattern).
    """
    cur.execute(
        """
        SELECT c.relname, pg_get_expr(c.relpartbound, c.oid)
        FROM pg_inherits i
        JOIN pg_class c ON c.oid = i.inhrelid
        WHERE i.inhparent = %s::regclass
        """,
        [table],
    )
    rows = cur.fetchall()
    result: list[tuple[str, datetime | None]] = []
    for name, expr in rows:
        upper = _parse_upper_bound(expr)
        result.append((name, upper))
    return result


def _parse_upper_bound(expr: str) -> datetime | None:
    """Parse the TO timestamp from a partition bound expression.

    Returns None for DEFAULT partitions or unrecognised expressions.
    """
    if not expr or "DEFAULT" in expr:
        return None
    match = _BOUND_TO_RE.search(expr)
    if not match:
        return None
    ts_str = match.group(1)
    # Parse ISO-style timestamps; handle +00 suffix variants.
    # Try common formats produced by pg_get_expr.
    for fmt in (
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
    ):
        try:
            dt = datetime.strptime(ts_str, fmt)
            # Ensure timezone-aware (UTC)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    # Fallback: try dateutil-style parse if the above all fail
    try:
        # Handle "+00" suffix by normalising to "+00:00"
        normalised = ts_str
        if normalised.endswith("+00"):
            normalised = normalised[:-3] + "+00:00"
        dt = datetime.fromisoformat(normalised)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Public callables
# ---------------------------------------------------------------------------


def ensure_future_partitions(cur, table: str, interval_str: str, premake: int) -> int:
    """Create the next *premake + 1* daily partitions of *table* if absent.

    Uses pg_try_advisory_lock so concurrent daemons skip rather than race.
    Catches the attach-conflict error (rows in DEFAULT overlapping the new
    range) per the operational constraint in constraints.md — logs a WARNING
    and continues the loop rather than propagating the error.

    Args:
        cur: psycopg cursor (autocommit or inside a transaction — caller's choice).
        table: Parent partitioned table name (unquoted).
        interval_str: Interval string accepted by PostgreSQL (e.g. "1 day").
        premake: Number of *additional* future partitions to create beyond the
                 current one (so premake=7 creates 8 partitions total).

    Returns:
        Number of partitions actually created in this call.
    """
    # --- advisory lock ---
    cur.execute("SELECT pg_try_advisory_lock(%s)", [ADVISORY_LOCK_ENSURE])
    (lock_acquired,) = cur.fetchone()
    if not lock_acquired:
        return 0

    created = 0
    try:
        for i in range(premake + 1):
            cur.execute(
                "SELECT date_trunc('day', now()) + (%s * %s::interval)",
                [i, interval_str],
            )
            (lo,) = cur.fetchone()
            # Compute hi in Python to avoid an extra round-trip
            cur.execute(
                "SELECT %s::timestamptz + %s::interval",
                [lo, interval_str],
            )
            (hi,) = cur.fetchone()
            # Old: name = "sqlery_queued_job_" + lo.strftime("%Y%m%d")
            # Sub-daily intervals need time precision to avoid same-day name collisions (WR-01).
            _suffix_fmt = "%Y%m%d_%H%M" if _is_sub_daily_interval(interval_str) else "%Y%m%d"
            name = table + "_" + lo.strftime(_suffix_fmt)
            try:
                cur.execute(
                    pgsql.SQL(
                        "CREATE TABLE IF NOT EXISTS {name}"
                        " PARTITION OF {table}"
                        " FOR VALUES FROM (%s) TO (%s)"
                    ).format(
                        name=pgsql.Identifier(name),
                        table=pgsql.Identifier(table),
                    ),
                    [lo, hi],
                )
                created += 1
            except Exception as exc:
                if not isinstance(exc, psycopg.DatabaseError):
                    # Unexpected error (not a DB error): re-raise so the finally
                    # block releases the advisory lock and the caller sees the error.
                    raise
                # Catch attach-conflict (InvalidTableDefinition, ExclusionViolation,
                # or any DatabaseError) — rows in DEFAULT overlap the new range.
                logger.warning(
                    "Partition attach conflict for %s: %s "
                    "— rows in DEFAULT partition, manual cleanup required",
                    name,
                    exc,
                )
                # Continue to the next iteration — do NOT re-raise.
                continue
    finally:
        cur.execute("SELECT pg_advisory_unlock(%s)", [ADVISORY_LOCK_ENSURE])

    return created


def reclaim_drained_partitions(
    cur, table: str, retention_str: str, archive_hook=None
) -> int:
    """Detach and drop partitions that are outside retention and have no live work.

    Skip rules (applied in order):
        1. upper_bound is None → DEFAULT partition — never drop.
        2. upper_bound > now(UTC) − retention → inside retention window — skip.
        3. Partition has queued or running rows → back-pressure invariant — skip.
        4. Advisory lock not acquired → skip the entire tick (returned 0 at top).

    Reclaim order per constraints.md:
        DETACH PARTITION → archive_hook (if provided) → DROP TABLE

    Args:
        cur: psycopg cursor.
        table: Parent partitioned table name (unquoted).
        retention_str: Retention interval string for PostgreSQL (e.g. "30 days").
        archive_hook: Optional callable ``(cur, partition_name) -> None`` invoked
                      after DETACH and before DROP.  Exceptions are caught, logged,
                      and the DROP proceeds regardless.

    Returns:
        Number of partitions dropped.
    """
    # --- advisory lock ---
    cur.execute("SELECT pg_try_advisory_lock(%s)", [ADVISORY_LOCK_RECLAIM])
    (lock_acquired,) = cur.fetchone()
    if not lock_acquired:
        return 0

    dropped = 0
    try:
        # Compute retention cutoff in Python — avoids extra DB round-trip.
        cur.execute("SELECT now() - %s::interval", [retention_str])
        (cutoff,) = cur.fetchone()

        partitions = _list_partitions(cur, table)

        for name, upper_bound in partitions:
            # Skip rule 1: DEFAULT partition
            if upper_bound is None:
                continue

            # Skip rule 2: inside retention window
            if upper_bound > cutoff:
                continue

            # Skip rule 3: back-pressure invariant — live work pins partition
            cur.execute(
                pgsql.SQL(
                    "SELECT EXISTS("
                    "SELECT 1 FROM {name}"
                    " WHERE status IN ('queued', 'running')"
                    ")"
                ).format(name=pgsql.Identifier(name)),
            )
            (has_live_work,) = cur.fetchone()
            if has_live_work:
                continue

            # --- Reclaim: DETACH → archive hook → DROP ---
            cur.execute(
                pgsql.SQL(
                    "ALTER TABLE {table} DETACH PARTITION {name}"
                ).format(
                    table=pgsql.Identifier(table),
                    name=pgsql.Identifier(name),
                )
            )

            if archive_hook is not None:
                try:
                    archive_hook(cur, name)
                except Exception as exc:
                    logger.error(
                        "archive_hook failed for partition %s: %s — proceeding with DROP",
                        name,
                        exc,
                    )

            cur.execute(
                pgsql.SQL("DROP TABLE {name}").format(name=pgsql.Identifier(name))
            )
            dropped += 1

    finally:
        cur.execute("SELECT pg_advisory_unlock(%s)", [ADVISORY_LOCK_RECLAIM])

    return dropped


def check_default_partition(cur, table: str) -> int:
    """Return the row count of the DEFAULT partition of *table*.

    A count > 0 is a standing alert: rows in the DEFAULT partition are never
    reclaimed by the normal partition-maintenance cycle.  Any row there means
    either (a) partitions were not pre-created far enough ahead, or (b) the
    inserted timestamp fell outside all partition boundaries.

    Args:
        cur: psycopg cursor.
        table: Parent partitioned table name (unquoted).

    Returns:
        Row count (>= 0).  Returns 0 if no DEFAULT partition exists.
    """
    # Find the DEFAULT partition name via the catalog.
    cur.execute(
        """
        SELECT c.relname, pg_get_expr(c.relpartbound, c.oid)
        FROM pg_inherits i
        JOIN pg_class c ON c.oid = i.inhrelid
        WHERE i.inhparent = %s::regclass
        """,
        [table],
    )
    rows = cur.fetchall()

    default_name: str | None = None
    for name, expr in rows:
        if expr and "DEFAULT" in expr:
            default_name = name
            break

    if default_name is None:
        return 0

    cur.execute(
        pgsql.SQL("SELECT COUNT(*) FROM {name}").format(
            name=pgsql.Identifier(default_name)
        )
    )
    (count,) = cur.fetchone()
    count = int(count)

    if count > 0:
        logger.warning(
            "DEFAULT partition %s holds %d rows"
            " — these are never reclaimed; manual re-insert or archive required",
            default_name,
            count,
        )

    return count
