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
    rotation = result.get("ready_rotation") or {}
    if rotation.get("status") == DEGRADED:
        parts.append(
            f"Ready-queue rotation is behind: no slot reclaimed for "
            f"{rotation.get('lag_seconds')}s (limit {rotation.get('threshold_seconds')}s). "
            "A transaction keeps the generation lock or a ready partition; the ready table "
            "behaves like the non-rotating one until it ends (see docs/TROUBLESHOOTING.md)."
        )
    return " ".join(parts)
