"""PostgreSQL xmin-horizon guard: decision logic and read-only metrics.

A long-lived transaction (or replication slot, prepared transaction, or standby
with ``hot_standby_feedback``) pins the xmin horizon: VACUUM cannot remove any dead
tuple newer than it, so a hot-update table such as ``sqlery_queued_job`` bloats no
matter how aggressively autovacuum runs. This module answers "is the horizon
pinned, or is the table already bloated?" for the health endpoints.

``assess_horizon`` is a pure decision function. ``check_horizon`` gathers the
inputs with one cheap read-only query and never raises: any failure is reported as
``"unknown"`` so a health check can never be broken by this probe.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)

OK = "ok"
DEGRADED = "degraded"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class HorizonThresholds:
    """Limits past which the horizon is reported as degraded. 0 disables a limit."""

    max_xmin_age_seconds: float = 300.0
    max_dead_tuple_ratio: float = 0.5
    # Dead-tuple ratios are noise on tiny tables: require at least this many dead rows.
    min_dead_tuples: int = 10_000
    # Replication slots / standbys expose only an xid age, not a time.
    max_slot_xid_age: int = 5_000_000


def thresholds_from_config(get: Callable[[str, Any], Any]) -> HorizonThresholds:
    """Build thresholds from settings (``get(name, default)``); bad values fall back."""
    d = HorizonThresholds()

    def _num(name, default, cast):
        try:
            value = cast(get(name, default))
            return value if value >= 0 else default
        except (TypeError, ValueError):
            return default

    return HorizonThresholds(
        max_xmin_age_seconds=_num("PG_XMIN_AGE_DEGRADED_SECONDS", d.max_xmin_age_seconds, float),
        max_dead_tuple_ratio=_num("PG_DEAD_TUPLE_RATIO_DEGRADED", d.max_dead_tuple_ratio, float),
        min_dead_tuples=_num("PG_DEAD_TUPLE_MIN", d.min_dead_tuples, int),
        max_slot_xid_age=_num("PG_SLOT_XID_AGE_DEGRADED", d.max_slot_xid_age, int),
    )


def assess_horizon(
    oldest_xmin_age_seconds: float | None,
    dead_tuple_ratio: float | None,
    thresholds: HorizonThresholds,
    *,
    dead_tuples: int | None = None,
    slot_xid_age: int | None = None,
) -> str:
    """Decide ``"ok"``, ``"degraded"`` or ``"unknown"``.

    Any input may be ``None`` (could not be measured). Degraded wins over unknown:
    one measurable breach is enough to degrade. With no measurable input at all the
    answer is ``"unknown"``, never ``"ok"``.
    """
    t = thresholds
    verdicts = []

    if oldest_xmin_age_seconds is not None:
        verdicts.append(
            t.max_xmin_age_seconds > 0 and oldest_xmin_age_seconds > t.max_xmin_age_seconds
        )
    if slot_xid_age is not None:
        verdicts.append(t.max_slot_xid_age > 0 and slot_xid_age > t.max_slot_xid_age)
    if dead_tuple_ratio is not None:
        enough_dead = dead_tuples is None or dead_tuples >= t.min_dead_tuples
        verdicts.append(
            t.max_dead_tuple_ratio > 0 and enough_dead and dead_tuple_ratio > t.max_dead_tuple_ratio
        )

    if not verdicts:
        return UNKNOWN
    return DEGRADED if any(verdicts) else OK


# One row: horizon holders (sessions with a snapshot OR an xid, prepared xacts,
# replication slots, standbys with feedback) and sqlery_queued_job dead/live tuples
# summed over the table and its partitions.
HORIZON_SQL = """
SELECT
  (SELECT COALESCE(EXTRACT(EPOCH FROM max(now() - COALESCE(xact_start, state_change))), 0)
     FROM pg_stat_activity
    WHERE (backend_xmin IS NOT NULL OR backend_xid IS NOT NULL)
      AND pid <> pg_backend_pid()),
  (SELECT COALESCE(EXTRACT(EPOCH FROM max(now() - prepared)), 0) FROM pg_prepared_xacts),
  (SELECT COALESCE(max(GREATEST(age(xmin), age(catalog_xmin))), 0) FROM pg_replication_slots),
  (SELECT COALESCE(max(age(backend_xmin)), 0) FROM pg_stat_replication),
  (SELECT COALESCE(sum(n_dead_tup), 0) FROM pg_stat_user_tables
    WHERE relname = 'sqlery_queued_job'
       OR relid IN (SELECT inhrelid FROM pg_inherits
                     WHERE inhparent = to_regclass('sqlery_queued_job'))),
  (SELECT COALESCE(sum(n_live_tup), 0) FROM pg_stat_user_tables
    WHERE relname = 'sqlery_queued_job'
       OR relid IN (SELECT inhrelid FROM pg_inherits
                     WHERE inhparent = to_regclass('sqlery_queued_job')))
"""


def metrics_from_row(row) -> dict:
    """Turn the ``HORIZON_SQL`` row into the metrics dict."""
    activity_age, prepared_age, slot_age, standby_age, dead, live = (float(v or 0) for v in row)
    dead_i = int(dead)
    return {
        "oldest_xmin_age_seconds": max(activity_age, prepared_age),
        "slot_xid_age": int(max(slot_age, standby_age)),
        "dead_tuples": dead_i,
        "live_tuples": int(live),
        "dead_tuple_ratio": dead / max(live, 1.0),
    }


def _query_django() -> tuple | None:
    from django.db import connection, transaction

    if connection.vendor != "postgresql":
        return None
    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = '3000'")
            cur.execute(HORIZON_SQL)
            return cur.fetchone()


def _query_sqlalchemy() -> tuple | None:
    from sqlalchemy import text

    from sqlery.fastapi_sqlery.database import get_engine

    engine = get_engine()
    if engine.dialect.name != "postgresql":
        return None
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL statement_timeout = '3000'"))
        return tuple(conn.execute(text(HORIZON_SQL)).fetchone())


# ---------------------------------------------------------------------------
# Drained live partitions that cannot be dropped (#156)
# ---------------------------------------------------------------------------

LIVE_TABLE_NAME = "sqlery_queued_job"
DEFAULT_LIVE_GRACE = "1 hour"
DEFAULT_STUCK_PARTITION_SECONDS = 900.0
# Candidates that get an exact emptiness probe per health call (oldest first).
MAX_PARTITION_PROBES = 10

LIVE_PARTITIONS_SQL = """
SELECT c.relname, pg_get_expr(c.relpartbound, c.oid), COALESCE(s.n_live_tup, 0), now()
  FROM pg_inherits i
  JOIN pg_class c ON c.oid = i.inhrelid
  LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
 WHERE i.inhparent = to_regclass('sqlery_queued_job')
"""


def assess_live_partitions(stuck_ages_seconds: list[float], max_age_seconds: float) -> str:
    """Decide ``"ok"``/``"degraded"`` from the ages (seconds past their drop deadline) of the
    drained live partitions that are still there. 0 disables the limit."""
    if max_age_seconds > 0 and any(age > max_age_seconds for age in stuck_ages_seconds):
        return DEGRADED
    return OK


def live_partition_candidates(
    rows, grace: timedelta, now: datetime | None = None
) -> tuple[list[tuple[str, float, int]], int]:
    """Split catalog rows ``(name, bound, n_live_tup[, now])`` into the partitions past their
    drop deadline (upper bound + grace).

    Returns ``(candidates, overdue_total)``: ``candidates`` are ``(name, age_seconds,
    estimated_live_rows)`` of the overdue partitions, oldest first; the DEFAULT partition and
    unparsable bounds never count.
    """
    from .partitioning import _parse_upper_bound

    overdue: list[tuple[str, float, int]] = []
    for row in rows:
        name, bound, live = row[0], row[1], row[2]
        row_now = now or (row[3] if len(row) > 3 else None) or datetime.now(timezone.utc)
        upper = _parse_upper_bound(bound)
        if upper is None:
            continue
        age = (row_now - (upper + grace)).total_seconds()
        if age > 0:
            overdue.append((name, age, int(live or 0)))
    overdue.sort(key=lambda item: -item[1])
    return overdue, len(overdue)


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def check_live_partitions(
    get_setting: Callable[[str, Any], Any], run_in_txn: Callable[[Callable], Any]
) -> dict | None:
    """Count and age the drained live partitions that were not dropped. Never raises.

    A partition is *drained but undroppable* when it is past its upper bound plus
    ``SQLERY_LIVE_PARTITION_GRACE`` and holds no row, yet still exists: something keeps it
    locked (an old snapshot that read it, a transaction that wrote it) or the maintainer is
    not running. Partitions that still hold rows are legitimately pinned and counted apart.
    Emptiness is probed exactly (``EXISTS``, 3 s cap) for the oldest few overdue partitions
    whose statistics claim no live row; the rest rely on ``n_live_tup``.
    """
    try:
        from .partitioning import DEFAULT_LIVE_PARTITION_GRACE, parse_interval

        grace = parse_interval(get_setting("SQLERY_LIVE_PARTITION_GRACE", DEFAULT_LIVE_GRACE))
        grace = grace or parse_interval(DEFAULT_LIVE_PARTITION_GRACE)
        try:
            limit = float(
                get_setting("PG_LIVE_PARTITION_STUCK_DEGRADED_SECONDS", DEFAULT_STUCK_PARTITION_SECONDS)
            )
            if limit < 0:
                limit = DEFAULT_STUCK_PARTITION_SECONDS
        except (TypeError, ValueError):
            limit = DEFAULT_STUCK_PARTITION_SECONDS

        rows = run_in_txn(lambda run: run(LIVE_PARTITIONS_SQL))
        if not rows:
            return None  # not partitioned: nothing to report
        overdue, _ = live_partition_candidates(rows, grace)
        stuck_ages: list[float] = []
        with_rows = 0
        probes = 0
        for name, age, live in overdue:
            if live > 0:
                with_rows += 1
                continue
            drained = True
            if probes < MAX_PARTITION_PROBES:
                probes += 1
                try:
                    probe = run_in_txn(
                        lambda run, n=name: run(
                            f"SELECT EXISTS (SELECT 1 FROM {_quote_ident(n)})"
                        )
                    )
                    drained = not probe[0][0]
                except Exception:  # dropped meanwhile, or too bloated to scan: not counted
                    continue
            if drained:
                stuck_ages.append(age)
            else:
                with_rows += 1
        return {
            "status": assess_live_partitions(stuck_ages, limit),
            "drained_undroppable": len(stuck_ages),
            "oldest_drained_age_seconds": int(max(stuck_ages)) if stuck_ages else 0,
            "overdue_with_rows": with_rows,
            "threshold_seconds": limit,
        }
    except Exception as exc:
        logger.warning("live partition probe failed: %s", exc)
        return {"status": UNKNOWN, "reason": f"probe_failed: {type(exc).__name__}"}


def _txn_runner_django():
    from django.db import connection, transaction

    if connection.vendor != "postgresql":
        return None

    def run_in_txn(fn):
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '3000'")

                def run(sql):
                    cur.execute(sql)
                    return cur.fetchall()

                return fn(run)

    return run_in_txn


def _txn_runner_sqlalchemy():
    from sqlalchemy import text

    from sqlery.fastapi_sqlery.database import get_engine

    engine = get_engine()
    if engine.dialect.name != "postgresql":
        return None

    def run_in_txn(fn):
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL statement_timeout = '3000'"))

            def run(sql):
                return [tuple(r) for r in conn.exec_driver_sql(sql).fetchall()]

            return fn(run)

    return run_in_txn


def check_horizon(get_setting: Callable[[str, Any], Any] | None = None) -> dict:
    """Measure and assess the xmin horizon. Never raises.

    Returns ``{"status": "ok"|"degraded"|"unknown", ...metrics}``; ``unknown`` carries
    a ``reason`` (non-PostgreSQL backend, or the query failure).
    """
    try:
        if get_setting is None:
            from sqlery.compat import get_config as get_setting
        from sqlery.compat import is_django_mode

        row = _query_django() if is_django_mode() else _query_sqlalchemy()
        if row is None:
            return {"status": UNKNOWN, "reason": "not_postgresql"}
        metrics = metrics_from_row(row)
        status = assess_horizon(
            metrics["oldest_xmin_age_seconds"],
            metrics["dead_tuple_ratio"],
            thresholds_from_config(get_setting),
            dead_tuples=metrics["dead_tuples"],
            slot_xid_age=metrics["slot_xid_age"],
        )
        result = {"status": status, **metrics}
        # Opt-in ready-queue rotation (#141): a lagging rotation degrades health too.
        from . import rotation

        ready_rotation = rotation.check_rotation(get_setting)
        if ready_rotation is not None:
            result["xmin_status"] = status
            result["ready_rotation"] = ready_rotation
            if ready_rotation["status"] == DEGRADED:
                result["status"] = DEGRADED
        # Drained live partitions that could not be dropped (#156).
        runner = _txn_runner_django() if is_django_mode() else _txn_runner_sqlalchemy()
        live = check_live_partitions(get_setting, runner) if runner is not None else None
        if live is not None:
            result.setdefault("xmin_status", status)
            result["live_partitions"] = live
            if live["status"] == DEGRADED:
                result["status"] = DEGRADED
        return result
    except Exception as exc:  # health must never crash on a probe failure
        logger.warning("xmin horizon probe failed: %s", exc)
        return {"status": UNKNOWN, "reason": f"probe_failed: {type(exc).__name__}"}


def describe(result: dict) -> str:
    """One-line human description of a degraded result for dashboards."""
    parts = []
    if result.get("xmin_status", result.get("status")) == DEGRADED:
        parts.append(
            f"PostgreSQL xmin horizon pinned or table bloated: oldest xmin holder "
            f"{int(result.get('oldest_xmin_age_seconds', 0))}s, slot xid age "
            f"{result.get('slot_xid_age', 0)}, sqlery_queued_job dead tuples "
            f"{result.get('dead_tuples', 0)} ({result.get('dead_tuple_ratio', 0):.0%} of live). "
            "Find and end the long transaction (see docs/TROUBLESHOOTING.md)."
        )
    live = result.get("live_partitions") or {}
    if live.get("status") == DEGRADED:
        parts.append(
            f"{live.get('drained_undroppable')} drained sqlery_queued_job partition(s) could not "
            f"be dropped, the oldest {live.get('oldest_drained_age_seconds')}s past its deadline "
            f"(limit {int(live.get('threshold_seconds', 0))}s): a transaction or snapshot keeps "
            "them locked, or partition maintenance is not running "
            "(see docs/TROUBLESHOOTING.md)."
        )
    rotation = result.get("ready_rotation") or {}
    if rotation.get("status") == DEGRADED:
        parts.append(
            f"Ready-queue rotation is behind: no slot reclaimed for "
            f"{rotation.get('lag_seconds')}s (limit {rotation.get('threshold_seconds')}s). "
            "A transaction keeps the generation lock or a ready partition; the ready table "
            "behaves like the non-rotating one until it ends (see docs/TROUBLESHOOTING.md)."
        )
    return " ".join(parts)
