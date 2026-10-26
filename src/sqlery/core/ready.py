"""Ready queue: the narrow set of claimable jobs (issue #139).

Claiming reads two small tables instead of scanning ``sqlery_queued_job``:

* ``sqlery_job_ready(slot, job_id, queue_name, priority, created_at, scheduled_at)``: one
  entry per claimable job. A claim deletes the entry; PostgreSQL partitions the table by
  ``slot`` (only slot 0 is written until rotation is enabled).
* ``sqlery_job_blocked(job_id, dependency_id, created_at, blocked_at)``: one row per
  dependency of a job whose dependencies are unmet. The job is out of the ready table, so
  claims no longer re-examine it; when a job reaches a terminal state its dependents are
  re-checked (``release_dependents``).

The invariants every path keeps:

* a queued job has an entry in ``ready`` or ``blocked`` (a missing entry is repaired by the
  periodic ``maintain_ready_queue`` sweep, so a lost entry delays a job, never loses it);
* an entry whose job is no longer queued is harmless: a claim verifies the job under the
  entry and drops the stale entry.

This module holds the framework-agnostic parts: names, DDL, the dependency judgement and the
start-up guard. The SQL that reads and writes the tables lives in the two backends.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..tables import JOB_BLOCKED, JOB_READY

READY_TABLE = JOB_READY
BLOCKED_TABLE = JOB_BLOCKED

# Slot of the ready table writers use. #141 (rotation) derives it from a sequence; until it is
# enabled every writer uses 0. Readers and deleters always look at every slot.
SLOT_CURRENT = 0
SLOTS = (0, 1, 2)

# Index every claim uses: (queue_name, priority DESC, created_at, job_id).
READY_CLAIM_INDEX = "sqlery_ready_claim_idx"
BLOCKED_DEPENDENCY_INDEX = "sqlery_blocked_dependency_idx"

# Hops followed along a retry chain (parent_job_id) before giving up.
MAX_CHAIN_HOPS = 1000

MISSING_TABLE_MESSAGE = (
    f"The ready-queue tables '{READY_TABLE}' / '{BLOCKED_TABLE}' do not exist, so this worker "
    "refuses to start: without them it could not find any job to claim. Run the database "
    "migrations (Django: 'python manage.py migrate sqlery'; standalone: 'sqlery-migrate' / "
    "'alembic upgrade head'). Stop all running workers before migrating: a worker started "
    "before the tables existed keeps claiming from the job table and cannot see the new entries."
)


class ReadyTablesMissingError(RuntimeError):
    """The ready-queue tables are missing: migrations have not been applied."""


def require_ready_tables(backend: Any) -> None:
    """Refuse to start a worker on a database that has no ready-queue tables.

    Raises:
        ReadyTablesMissingError: with the migration instructions.
    """
    check = getattr(backend, "ready_tables_exist", None)
    if check is None:
        return
    if not check():
        raise ReadyTablesMissingError(MISSING_TABLE_MESSAGE)


# --------------------------------------------------------------------------------------------
# DDL (shared by the Alembic revision, the standalone bootstrap and the Django migration)
# --------------------------------------------------------------------------------------------


def postgres_ready_ddl() -> list[str]:
    """PostgreSQL DDL: ready table ``PARTITION BY LIST (slot)`` with partitions 0, 1, 2."""
    statements = [
        f"""
        CREATE TABLE IF NOT EXISTS {READY_TABLE} (
            slot         SMALLINT     NOT NULL DEFAULT 0,
            job_id       BIGINT       NOT NULL,
            queue_name   VARCHAR(50)  NOT NULL,
            priority     INTEGER      NOT NULL DEFAULT 0,
            created_at   TIMESTAMPTZ  NOT NULL,
            scheduled_at TIMESTAMPTZ,
            PRIMARY KEY (slot, job_id)
        ) PARTITION BY LIST (slot)
        """
    ]
    for slot in SLOTS:
        statements.append(
            f"CREATE TABLE IF NOT EXISTS {READY_TABLE}_{slot} "
            f"PARTITION OF {READY_TABLE} FOR VALUES IN ({slot})"
        )
    statements.append(
        f"CREATE INDEX IF NOT EXISTS {READY_CLAIM_INDEX} "
        f"ON {READY_TABLE} (queue_name, priority DESC, created_at, job_id)"
    )
    statements.extend(postgres_blocked_ddl())
    return statements


def postgres_blocked_ddl() -> list[str]:
    return [
        f"""
        CREATE TABLE IF NOT EXISTS {BLOCKED_TABLE} (
            job_id        BIGINT      NOT NULL,
            dependency_id BIGINT      NOT NULL,
            created_at    TIMESTAMPTZ NOT NULL,
            blocked_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (job_id, dependency_id)
        )
        """,
        f"CREATE INDEX IF NOT EXISTS {BLOCKED_DEPENDENCY_INDEX} "
        f"ON {BLOCKED_TABLE} (dependency_id)",
    ]


def sqlite_ready_ddl() -> list[str]:
    """SQLite DDL: plain tables (no partitioning)."""
    return [
        f"""
        CREATE TABLE IF NOT EXISTS {READY_TABLE} (
            slot         SMALLINT NOT NULL DEFAULT 0,
            job_id       BIGINT   NOT NULL,
            queue_name   VARCHAR(50) NOT NULL,
            priority     INTEGER  NOT NULL DEFAULT 0,
            created_at   DATETIME NOT NULL,
            scheduled_at DATETIME,
            PRIMARY KEY (slot, job_id)
        )
        """,
        f"CREATE INDEX IF NOT EXISTS {READY_CLAIM_INDEX} "
        f"ON {READY_TABLE} (queue_name, priority DESC, created_at, job_id)",
        f"""
        CREATE TABLE IF NOT EXISTS {BLOCKED_TABLE} (
            job_id        BIGINT   NOT NULL,
            dependency_id BIGINT   NOT NULL,
            created_at    DATETIME NOT NULL,
            blocked_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (job_id, dependency_id)
        )
        """,
        f"CREATE INDEX IF NOT EXISTS {BLOCKED_DEPENDENCY_INDEX} "
        f"ON {BLOCKED_TABLE} (dependency_id)",
    ]


def backfill_ready_sql() -> str:
    """Data migration: every queued job gets a ready entry (claims move blocked ones out).

    Idempotent (``ON CONFLICT DO NOTHING`` is understood by PostgreSQL and SQLite >= 3.24).
    """
    return (
        f"INSERT INTO {READY_TABLE} (slot, job_id, queue_name, priority, created_at, "
        f"scheduled_at) SELECT {SLOT_CURRENT}, id, queue_name, priority, created_at, "
        f"scheduled_at FROM sqlery_queued_job WHERE status = 'queued' "
        f"ON CONFLICT DO NOTHING"
    )


# --------------------------------------------------------------------------------------------
# Decisions
# --------------------------------------------------------------------------------------------

MET = "met"
PENDING = "pending"
FAILED = "failed"


@dataclass(frozen=True)
class DependencyVerdict:
    """What a job's dependencies decide: run it, keep it waiting, or fail it."""

    state: str
    failed: list = field(default_factory=list)

    @property
    def runnable(self) -> bool:
        return self.state == MET


def dependency_ids(job: Any) -> list[int]:
    """The job's dependency ids as ints, without duplicates and in order."""
    seen: list[int] = []
    for raw in getattr(job, "dependencies", None) or []:
        try:
            dep = int(raw)
        except (TypeError, ValueError):
            continue
        if dep not in seen:
            seen.append(dep)
    return seen


def judge_dependencies(
    deps: list,
    fetch: Callable[[Any], Any],
    latest_retry: Callable[[Any], Any],
    is_retry_pending: Callable[[Any], bool] | None = None,
) -> DependencyVerdict:
    """Decide the fate of a job from the state of its dependencies.

    Args:
        deps: Dependency job ids.
        fetch: ``fetch(id)`` returns the dependency job (``status``, ``termination_reason``)
            or None when it no longer exists.
        latest_retry: ``latest_retry(id)`` returns the newest retry child of a job or None.
        is_retry_pending: ``is_retry_pending(reason)``; a failed dependency tagged as owing
            a retry that does not exist yet is still pending, not failed.

    A failed or archived dependency is judged by its newest retry attempt, so a retrying
    step neither fails nor blocks its dependents for good. A missing dependency fails the
    job. Anything not yet successful keeps the job pending.
    """
    failed: list = []
    pending = False
    for dep_id in deps:
        dep = fetch(dep_id)
        if dep is None:
            failed.append(dep_id)
            continue
        hops = 0
        while getattr(dep, "status", None) in ("failed", "archived") and hops < MAX_CHAIN_HOPS:
            child = latest_retry(dep.id)
            if child is None:
                break
            dep, hops = child, hops + 1
        status = getattr(dep, "status", None)
        if status == "success":
            continue
        if status == "failed":
            reason = getattr(dep, "termination_reason", "") or ""
            if is_retry_pending is not None and is_retry_pending(reason):
                pending = True  # its retry is owed but not created yet
            else:
                failed.append(dep.id)
            continue
        pending = True  # queued / running / archived without a retry / anything else
    if failed:
        return DependencyVerdict(FAILED, failed)
    return DependencyVerdict(PENDING if pending else MET)


class Throttle:
    """Run something at most once per ``interval`` seconds (per instance)."""

    def __init__(self, interval: float):
        self.interval = interval
        self._last: float | None = None

    def due(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        if self.interval <= 0:
            return False
        if self._last is not None and now - self._last < self.interval:
            return False
        self._last = now
        return True
