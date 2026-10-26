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
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg import sql as pgsql

__all__ = [
    "ensure_future_partitions",
    "reclaim_drained_partitions",
    "check_default_partition",
    "is_partitioned",
    "decide_partition_reclaim",
    "ensure_live_partitions",
    "reclaim_drained_live_partitions",
    "parse_interval",
    "DEFAULT_LIVE_PARTITION_INTERVAL",
    "DEFAULT_LIVE_PARTITION_GRACE",
    "MIN_LIVE_PARTITION_GRACE_SECONDS",
    "LIVE_LOOKAHEAD",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Advisory-lock constants (stable int64s — documented above)
# ---------------------------------------------------------------------------

# Derived from ASCII bytes of the 8-char tags below; both fit in signed int8.
ADVISORY_LOCK_ENSURE: int = int.from_bytes(b"SQLEPART", "big")   # partition ensure DDL lock
ADVISORY_LOCK_RECLAIM: int = int.from_bytes(b"SQLERCLA", "big")  # partition reclaim DDL lock

# Regex to extract the TO ('...') timestamp from a partition bound expression.
_BOUND_TO_RE = re.compile(r"TO \('([^']+)'\)")
_BOUND_FROM_RE = re.compile(r"FROM \('([^']+)'\)")

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


def is_partitioned(cur, table: str) -> bool:
    """True iff *table* exists and is a partitioned table (``relkind = 'p'``)."""
    cur.execute(
        "SELECT relkind = 'p' FROM pg_class "
        "WHERE relname = %s AND relnamespace = current_schema()::regnamespace",
        [table],
    )
    row = cur.fetchone()
    return bool(row and row[0])


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


def _list_partition_ranges(cur, table: str) -> list[tuple[datetime, datetime]]:
    """Return (lower, upper) bounds for every range child partition of *table*."""
    cur.execute(
        """
        SELECT pg_get_expr(c.relpartbound, c.oid)
        FROM pg_inherits i
        JOIN pg_class c ON c.oid = i.inhrelid
        WHERE i.inhparent = %s::regclass
        """,
        [table],
    )
    ranges: list[tuple[datetime, datetime]] = []
    for (expr,) in cur.fetchall():
        match = _BOUND_FROM_RE.search(expr or "")
        lower = _parse_ts(match.group(1)) if match else None
        upper = _parse_upper_bound(expr)
        if lower is not None and upper is not None:
            ranges.append((lower, upper))
    return ranges


def _parse_upper_bound(expr: str) -> datetime | None:
    """Parse the TO timestamp from a partition bound expression.

    Returns None for DEFAULT partitions or unrecognised expressions.
    """
    if not expr or "DEFAULT" in expr:
        return None
    match = _BOUND_TO_RE.search(expr)
    if not match:
        return None
    return _parse_ts(match.group(1))


def _parse_ts(ts_str: str) -> datetime | None:
    """Parse a timestamp literal from a partition bound expression."""
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
    """Create the next *premake + 1* partitions of *table* if absent.

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
        if _is_sub_daily_interval(interval_str):
            # Anchor at the current interval bucket (epoch-aligned), not at midnight, so the
            # first partition contains now().
            lo_sql = (
                "SELECT to_timestamp(floor(extract(epoch FROM now())"
                " / extract(epoch FROM %(iv)s::interval))"
                " * extract(epoch FROM %(iv)s::interval))"
                " + (%(i)s * %(iv)s::interval)"
            )
        else:
            lo_sql = "SELECT date_trunc('day', now()) + (%(i)s * %(iv)s::interval)"
        existing = _list_partition_ranges(cur, table)
        for i in range(premake + 1):
            cur.execute(lo_sql, {"i": i, "iv": interval_str})
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
            # Skip ranges already covered by an existing partition (e.g. the daily partitions
            # created by migration 0030): CREATE ... PARTITION OF would fail with an overlap
            # error and abort the surrounding transaction.
            if any(elo < hi and lo < ehi for elo, ehi in existing):
                continue
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
                existing.append((lo, hi))
            # Old: except Exception as exc: if not isinstance(exc, psycopg.DatabaseError): raise
            # Re-raise connection-level errors so callers see real failures (WR-03).
            except (psycopg.OperationalError, psycopg.InterfaceError):
                raise
            except (
                psycopg.errors.InvalidTableDefinition,
                psycopg.errors.CheckViolation,
                psycopg.errors.ExclusionViolation,
            ) as exc:
                # Attach-conflict: rows in DEFAULT partition overlap the new range.
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


# Default "live work" test of rule 3 below (the job table).
LIVE_WORK_PREDICATE = "status IN ('queued', 'running')"


def reclaim_drained_partitions(
    cur,
    table: str,
    retention_str: str,
    archive_hook=None,
    live_predicate: str | None = LIVE_WORK_PREDICATE,
) -> int:
    """Detach and drop partitions that are outside retention and have no live work.

    Skip rules (applied in order):
        1. upper_bound is None → DEFAULT partition — never drop.
        2. upper_bound > now(UTC) − retention → inside retention window — skip.
        3. Partition has rows matching ``live_predicate`` (queued or running jobs by default;
           an open attempt for the attempt table; ``None`` for the insert-only job history,
           which has no live work) → back-pressure invariant — skip.
        4. Advisory lock not acquired → skip the entire tick (returned 0 at top).

    ``live_predicate`` is trusted SQL (module constants only, never user input).

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
            if live_predicate:
                cur.execute(
                    pgsql.SQL("SELECT EXISTS(SELECT 1 FROM {name} WHERE {pred})").format(
                        name=pgsql.Identifier(name), pred=pgsql.SQL(live_predicate)
                    ),
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

            # Old: cur.execute(pgsql.SQL("DROP TABLE {name}").format(...))
            # Wrap in its own try/except: if archive_hook aborted the transaction,
            # DROP fails but we must log and continue rather than crash the loop (WR-02).
            try:
                cur.execute(
                    pgsql.SQL("DROP TABLE {name}").format(name=pgsql.Identifier(name))
                )
                dropped += 1
            except psycopg.DatabaseError as drop_exc:
                logger.error(
                    "DROP TABLE failed for detached partition %s: %s — manual cleanup required",
                    name,
                    drop_exc,
                )

    finally:
        cur.execute("SELECT pg_advisory_unlock(%s)", [ADVISORY_LOCK_RECLAIM])

    return dropped


# ---------------------------------------------------------------------------
# Live-table partitions (#156): short partitions, dropped as soon as drained
# ---------------------------------------------------------------------------
#
# ``sqlery_queued_job`` only ever holds work in flight: finished jobs move to the insert-only
# history. A ``created_at`` partition that has passed and holds no row at all therefore
# contains nothing but dead tuples, and with the xmin horizon pinned VACUUM cannot clear
# them. Dropping the whole partition is the only reclaim that does not depend on VACUUM, so
# it must not wait for ``SQLERY_PARTITION_RETENTION`` (which is for history).

DEFAULT_LIVE_PARTITION_INTERVAL = "1 hour"
DEFAULT_LIVE_PARTITION_GRACE = "1 hour"
# A grace shorter than this is raised to it: the margin covers in-flight inserts and clock skew.
MIN_LIVE_PARTITION_GRACE_SECONDS = 60.0
# Partitions are always premade at least this far ahead, whatever the interval, so a stopped
# maintainer does not push inserts into the DEFAULT partition (which is never reclaimed).
LIVE_LOOKAHEAD = "1 day"

# Lock budget for the parent table: claims and enqueues queue behind a pending
# ACCESS EXCLUSIVE request, so the request is abandoned after this long.
RECLAIM_LOCK_TIMEOUT_MS = 50
# Ceiling for the emptiness scans (a partition bloated with dead tuples is scanned in full).
RECLAIM_STATEMENT_TIMEOUT_MS = 5000

_INTERVAL_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*(second|sec|minute|min|hour|day|week)s?\s*$", re.IGNORECASE
)
_INTERVAL_UNIT_SECONDS = {
    "second": 1, "sec": 1, "minute": 60, "min": 60, "hour": 3600, "day": 86400, "week": 604800,
}

# Outcomes of decide_partition_reclaim.
DROP = "drop"
CHECK_ROWS = "check_rows"  # undecided: look whether the partition holds any row
TAKE_LOCK = "take_lock"  # undecided: take the partition lock
SKIP_DEFAULT = "skip_default"
SKIP_GRACE = "skip_grace"
SKIP_HAS_ROWS = "skip_has_rows"
SKIP_LOCKED = "skip_locked"


def parse_interval(value) -> timedelta | None:
    """Parse ``'1 hour'`` / ``'90 minutes'`` / ``'2 days'`` into a timedelta; None if invalid.

    Months and years are rejected on purpose: their length depends on the calendar, and a
    grace period or live partition width is a fixed duration.
    """
    match = _INTERVAL_RE.match(str(value)) if value is not None else None
    if not match:
        return None
    seconds = float(match.group(1)) * _INTERVAL_UNIT_SECONDS[match.group(2).lower()]
    return timedelta(seconds=seconds) if seconds > 0 else None


def decide_partition_reclaim(
    upper_bound: datetime | None,
    now: datetime,
    grace: timedelta,
    has_rows: bool | None,
    is_default: bool,
    lock_ok: bool | None,
) -> str:
    """Pure decision for one live-table partition: what to do next.

    A partition is dropped only when it is not the DEFAULT partition, its upper bound is older
    than ``now - grace``, it holds **no row at all** (rows of every status pin it: queued,
    running, retry-pending, webhook-pending, ...) and its lock was obtained. ``has_rows`` and
    ``lock_ok`` are ``None`` while unknown; the answer then names the next step
    (``check_rows`` / ``take_lock``), so the caller does the cheap checks first and takes the
    strong lock last. Rows are checked again after the lock is held: call this once more with
    the fresh ``has_rows`` and ``lock_ok=True``.

    Returns one of ``drop``, ``check_rows``, ``take_lock``, ``skip_default``, ``skip_grace``,
    ``skip_has_rows``, ``skip_locked``.
    """
    if is_default or upper_bound is None:
        return SKIP_DEFAULT
    if upper_bound >= now - grace:
        return SKIP_GRACE
    if has_rows is None:
        return CHECK_ROWS
    if has_rows:
        return SKIP_HAS_ROWS
    if lock_ok is None:
        return TAKE_LOCK
    if not lock_ok:
        return SKIP_LOCKED
    return DROP


def _end_implicit_transaction(conn) -> bool:
    """Commit a transaction psycopg opened implicitly, unless a framework owns it.

    The standalone backend's raw connection is not in autocommit mode, so the partition DDL of
    the same tick (which holds locks until commit) is still pending when reclaim starts.
    Returns False when the connection is inside a Django ``atomic`` block: that transaction is
    not ours to end.
    """
    from psycopg.pq import TransactionStatus

    if conn.autocommit or conn.info.transaction_status != TransactionStatus.INTRANS:
        return True
    try:
        from django.conf import settings as dj_settings

        if dj_settings.configured:
            from django.db import connection as dj_conn

            if dj_conn.in_atomic_block:
                return False
    except Exception:  # Django absent or not set up: the transaction is psycopg's own
        pass
    conn.commit()
    return True


def _partition_has_rows(cur, name: str) -> bool:
    cur.execute(pgsql.SQL("SELECT EXISTS (SELECT 1 FROM {name})").format(name=pgsql.Identifier(name)))
    return bool(cur.fetchone()[0])


def _try_reclaim_one(cur, conn, table: str, name: str, upper: datetime, now: datetime,
                     grace: timedelta) -> str:
    """Drop one live partition if it is safe; returns the decision that was reached.

    1. Cheap unlocked look: a partition with any row is left alone without taking a lock.
    2. In a transaction: ``lock_timeout`` small, ``LOCK TABLE part ACCESS EXCLUSIVE NOWAIT``
       as the first statement (so even a REPEATABLE READ transaction sees committed rows
       afterwards), re-check emptiness under the lock, ``DETACH`` then ``DROP``.
    Any lock that cannot be had at once rolls the transaction back; the next tick retries.
    """
    first = decide_partition_reclaim(upper, now, grace, has_rows=None, is_default=False,
                                     lock_ok=None)
    if first != CHECK_ROWS:
        return first

    try:
        with conn.transaction():
            cur.execute(f"SET LOCAL statement_timeout = {int(RECLAIM_STATEMENT_TIMEOUT_MS)}")
            has_rows = _partition_has_rows(cur, name)
    except psycopg.errors.QueryCanceled:
        return SKIP_HAS_ROWS  # too bloated to scan in time: assume rows, look again next tick
    step = decide_partition_reclaim(upper, now, grace, has_rows, False, None)
    if step != TAKE_LOCK:
        return step

    from psycopg.pq import TransactionStatus

    nested = conn.info.transaction_status != TransactionStatus.IDLE
    try:
        with conn.transaction():
            cur.execute(f"SET LOCAL lock_timeout = {int(RECLAIM_LOCK_TIMEOUT_MS)}")
            cur.execute(f"SET LOCAL statement_timeout = {int(RECLAIM_STATEMENT_TIMEOUT_MS)}")
            cur.execute("SHOW transaction_isolation")  # a utility command: takes no snapshot
            if nested and cur.fetchone()[0] != "read committed":
                # Inside someone else's snapshot transaction the re-check below could miss
                # a row committed after that snapshot: never drop on that evidence.
                return SKIP_LOCKED
            part = pgsql.Identifier(name)
            cur.execute(pgsql.SQL("LOCK TABLE {name} IN ACCESS EXCLUSIVE MODE NOWAIT").format(
                name=part))
            # Nothing can write the partition now: this check is final.
            has_rows = _partition_has_rows(cur, name)
            step = decide_partition_reclaim(upper, now, grace, has_rows, False, True)
            if step != DROP:
                return step  # leaving the block commits nothing but the lock release
            cur.execute(pgsql.SQL("ALTER TABLE {table} DETACH PARTITION {name}").format(
                table=pgsql.Identifier(table), name=part))
            cur.execute(pgsql.SQL("DROP TABLE {name}").format(name=part))
            return DROP
    except (psycopg.errors.LockNotAvailable, psycopg.errors.DeadlockDetected,
            psycopg.errors.QueryCanceled):
        return SKIP_LOCKED


def reclaim_drained_live_partitions(cur, table: str, grace_str: str | None = None) -> int:
    """Drop the drained partitions of the live job table without waiting for retention.

    A partition is dropped when it is not the DEFAULT partition, its upper bound is older than
    ``now - grace`` and it holds no row at all (see ``decide_partition_reclaim``). The
    emptiness check and the drop are made safe against a concurrent insert by taking the
    partition's ACCESS EXCLUSIVE lock first (NOWAIT), checking again under it and detaching
    and dropping in that same transaction.

    Claims and enqueues never queue behind this: the partition lock is NOWAIT, the parent lock
    that ``DETACH PARTITION`` needs is given ``lock_timeout`` 50 ms, and a failure to get
    either rolls back and leaves the partition for the next tick. ``DETACH ... CONCURRENTLY``
    is not used: it cannot run on a table with a DEFAULT partition (which this table has, to
    catch stray ``created_at`` values) nor inside a transaction block, and it can leave a
    half-detached partition behind if interrupted.

    Only one maintainer runs this at a time (advisory lock). Returns the number dropped; any
    per-partition failure is logged and skipped, never raised.
    """
    grace = parse_interval(grace_str if grace_str is not None else DEFAULT_LIVE_PARTITION_GRACE)
    if grace is None:
        logger.warning("invalid SQLERY_LIVE_PARTITION_GRACE %r: using %s", grace_str,
                       DEFAULT_LIVE_PARTITION_GRACE)
        grace = parse_interval(DEFAULT_LIVE_PARTITION_GRACE)
    grace = max(grace, timedelta(seconds=MIN_LIVE_PARTITION_GRACE_SECONDS))

    cur.execute("SELECT pg_try_advisory_lock(%s)", [ADVISORY_LOCK_RECLAIM])
    (lock_acquired,) = cur.fetchone()
    if not lock_acquired:
        return 0

    dropped = 0
    try:
        conn = cur.connection
        if not _end_implicit_transaction(conn):
            logger.debug("live partition reclaim skipped: caller owns an open transaction")
            return 0
        cur.execute("SELECT now()")
        (now,) = cur.fetchone()
        partitions = [(n, ub) for n, ub in _list_partitions(cur, table) if ub is not None]
        for name, upper in sorted(partitions, key=lambda item: item[1]):
            if upper >= now - grace:
                continue  # the list is ordered: nothing newer qualifies either
            try:
                outcome = _try_reclaim_one(cur, conn, table, name, upper, now, grace)
            except (psycopg.OperationalError, psycopg.InterfaceError):
                if conn.closed or conn.broken:
                    raise
                logger.warning("live partition %s: reclaim failed, will retry", name,
                               exc_info=True)
                continue
            except psycopg.Error:
                logger.warning("live partition %s: reclaim failed, will retry", name,
                               exc_info=True)
                continue
            if outcome == DROP:
                dropped += 1
                logger.info("dropped drained live partition %s", name)
            elif outcome == SKIP_LOCKED:
                logger.info("live partition %s is drained but locked; retrying next tick", name)
    finally:
        cur.execute("SELECT pg_advisory_unlock(%s)", [ADVISORY_LOCK_RECLAIM])
    return dropped


def ensure_live_partitions(
    cur,
    table: str,
    interval_str: str | None = None,
    premake: int = 7,
    lookahead_str: str = LIVE_LOOKAHEAD,
) -> int:
    """Create live-table partitions so that coverage runs ahead of now for long enough.

    Coverage reaches at least ``max(premake x interval, lookahead)`` ahead, so the default
    ``'1 hour'`` interval keeps a day of partitions premade. Existing partitions of any width
    are respected: the walk starts at the interval bucket holding now, jumps over every range
    already covered, and clips a new partition to the next existing one, so a changed interval
    (or the daily partitions of an older install) never overlaps and never leaves a gap that
    would send inserts to the DEFAULT partition. Existing partitions are never touched.
    """
    width = parse_interval(interval_str or DEFAULT_LIVE_PARTITION_INTERVAL)
    if width is None or width < timedelta(minutes=1):
        logger.warning("invalid SQLERY_LIVE_PARTITION_INTERVAL %r: using %s", interval_str,
                       DEFAULT_LIVE_PARTITION_INTERVAL)
        interval_str = DEFAULT_LIVE_PARTITION_INTERVAL
        width = parse_interval(interval_str)
    interval_str = interval_str or DEFAULT_LIVE_PARTITION_INTERVAL
    ahead = max(width * max(int(premake), 1), parse_interval(lookahead_str) or timedelta(days=1))

    cur.execute("SELECT pg_try_advisory_lock(%s)", [ADVISORY_LOCK_ENSURE])
    (lock_acquired,) = cur.fetchone()
    if not lock_acquired:
        return 0

    created = 0
    try:
        conn = cur.connection
        cur.execute("SELECT now()")
        (now,) = cur.fetchone()
        seconds = width.total_seconds()
        if width >= timedelta(days=1) and width.total_seconds() % 86400 == 0:
            cur.execute("SELECT date_trunc('day', now())")
        else:
            cur.execute("SELECT to_timestamp(floor(extract(epoch FROM now()) / %s) * %s)",
                        [seconds, seconds])
        (cursor_ts,) = cur.fetchone()
        end = now + ahead
        existing = sorted(_list_partition_ranges(cur, table))
        suffix_fmt = "%Y%m%d_%H%M" if width < timedelta(days=1) else "%Y%m%d"
        for _ in range(10_000):  # hard stop for a pathological interval
            if cursor_ts >= end:
                break
            covering = next((hi for lo, hi in existing if lo <= cursor_ts < hi), None)
            if covering is not None:
                cursor_ts = covering
                continue
            hi = cursor_ts + width
            following = [lo for lo, _ in existing if lo > cursor_ts]
            if following and min(following) < hi:
                hi = min(following)
            name = table + "_" + cursor_ts.astimezone(timezone.utc).strftime(suffix_fmt)
            try:
                with conn.transaction():
                    # DDL takes no bind parameters: the bounds are inlined as literals.
                    cur.execute(
                        pgsql.SQL(
                            "CREATE TABLE IF NOT EXISTS {name} PARTITION OF {table}"
                            " FOR VALUES FROM ({lo}) TO ({hi})"
                        ).format(
                            name=pgsql.Identifier(name),
                            table=pgsql.Identifier(table),
                            lo=pgsql.Literal(cursor_ts),
                            hi=pgsql.Literal(hi),
                        )
                    )
                created += 1
            except (psycopg.errors.InvalidTableDefinition, psycopg.errors.CheckViolation,
                    psycopg.errors.ExclusionViolation) as exc:
                # Rows already in the DEFAULT partition fall in this range.
                logger.warning(
                    "Partition attach conflict for %s: %s "
                    "— rows in DEFAULT partition, manual cleanup required", name, exc)
            existing.append((cursor_ts, hi))
            existing.sort()
            cursor_ts = hi
    finally:
        cur.execute("SELECT pg_advisory_unlock(%s)", [ADVISORY_LOCK_ENSURE])
    return created


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
