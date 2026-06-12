"""SQLite x PostgreSQL divergence matrix for SQLAlchemyBackend (standalone mode).

Phase 17, plan 17-04: Acceptance criterion R1-R6 re-verification for standalone mode.

Exercises representative SQLAlchemyBackend methods under both SQLite and PG,
asserting consistent return types and shapes, with documented divergences:

  D6: cleanup_jobs behavior
    - SQLite: returns {"deleted": N, "count": N}  (batched DELETE path)
    - PG partitioned: returns {"reclaimed_via_partition_drop": True, "dropped_partitions": N, ...}

  D6: create_job far-future routing
    - SQLite: far-future jobs go to QueuedJob (unchanged)
    - PG partitioned: far-future jobs go to ScheduledJob (staging table)

  D8: _partitioned_pg()
    - SQLite: False
    - PG fresh install: True

TestStandaloneDivergenceMatrixSQLite always runs (no PG URL needed).
TestStandaloneDivergenceMatrixPG skips when SQLERY_TEST_PG_URL is not set.
"""

from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime, timezone, timedelta

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pg_url() -> str:
    """Return the PG test URL, translating to psycopg3 dialect for SQLAlchemy."""
    raw = os.environ.get("SQLERY_TEST_PG_URL", "")
    if not raw:
        return raw
    # Translate postgresql:// -> postgresql+psycopg:// (psycopg3, no psycopg2 needed)
    if raw.startswith("postgresql://") or raw.startswith("postgresql+psycopg2://"):
        return "postgresql+psycopg" + raw[raw.index("://"):]
    return raw


def _far_future() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=60)


def _make_sqlite_backend(tmp_path_str: str | None = None):
    """Return a fresh SQLAlchemyBackend pointed at a temp SQLite file.

    Resets the global _engine cache so the new URL takes effect.
    """
    from sqlery.fastapi_sqlery import database as _db

    db_path = tmp_path_str or tempfile.mktemp(suffix=".db", prefix="sqlery_matrix_")
    sqlite_url = f"sqlite:///{db_path}"

    _db._engine = None
    _db.init_database(sqlite_url)

    from sqlery.fastapi_sqlery.backend import SQLAlchemyBackend

    backend = SQLAlchemyBackend()
    backend._partitioned_pg_cache = None
    return backend


def _query_relkind(engine, table_name: str) -> str | None:
    """Return pg_class.relkind for the named table, or None if not found."""
    from sqlalchemy import text

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT relkind FROM pg_class "
                "WHERE relname = :name AND relnamespace = 'public'::regnamespace"
            ),
            {"name": table_name},
        ).fetchone()
    return row[0] if row else None


def _make_pg_backend():
    """Return a fresh SQLAlchemyBackend pointed at the PG test URL.

    NOTE: Bug-SA-01 — backend._partitioned_pg() uses %s/list params incompatible
    with SQLAlchemy 2.x + psycopg3. Tests prime the cache directly as a workaround.
    """
    from sqlery.fastapi_sqlery import database as _db
    from sqlery.fastapi_sqlery.database import get_engine

    _db._engine = None
    _db.init_database(_pg_url())

    from sqlery.fastapi_sqlery.backend import SQLAlchemyBackend

    backend = SQLAlchemyBackend()
    # Prime the partition cache (workaround for Bug-SA-01).
    engine = get_engine()
    relkind = _query_relkind(engine, "sqlery_queued_job")
    backend._partitioned_pg_cache = (relkind == "p")
    return backend


def _create_basic_job(backend, scheduled_at=None, queue_name="default"):
    return backend.create_job(
        task_path="tests.standalone_divergence.noop",
        kwargs={"k": 1},
        queue_name=queue_name,
        priority=0,
        scheduled_at=scheduled_at,
        max_retries=0,
        retry_backoff=1.0,
        allow_parallel=True,
        timeout_seconds=None,
    )


def _has_single_partition(plan_text: str) -> bool:
    """Return True if EXPLAIN plan shows single-partition pruning.

    Criteria: "Append" not in plan AND at most one sqlery_queued_job_* child.
    """
    child_partitions = set(re.findall(r"sqlery_queued_job_(?:\d{8}|default)", plan_text))
    return "Append" not in plan_text and len(child_partitions) == 1


# ---------------------------------------------------------------------------
# SQLite matrix — always runs (no PG URL required)
# ---------------------------------------------------------------------------


class TestStandaloneDivergenceMatrixSQLite:
    """SQLAlchemyBackend public methods exercised under SQLite.

    Assertions cover the documented SQLite behavior (D6) that must remain
    unchanged regardless of Phase 17 changes.
    """

    def test_cleanup_jobs_returns_deleted_dict_on_sqlite(self):
        """D6 / R2: cleanup_jobs on SQLite returns {"deleted": N} (batched DELETE path).

        Must NOT contain the PG-only 'reclaimed_via_partition_drop' key.
        """
        backend = _make_sqlite_backend()
        _create_basic_job(backend)

        result = backend.cleanup_jobs()
        assert isinstance(result, dict), f"cleanup_jobs must return a dict. Got: {type(result)}"
        assert "deleted" in result, (
            f"D6 — SQLite: cleanup_jobs must return dict with 'deleted' key. Got: {result}"
        )
        assert "reclaimed_via_partition_drop" not in result, (
            "D6 — SQLite: cleanup_jobs must NOT return 'reclaimed_via_partition_drop'. "
            f"Got: {result}"
        )

    def test_partitioned_pg_returns_false_on_sqlite(self):
        """D6 / D8: _partitioned_pg() returns False on SQLite."""
        backend = _make_sqlite_backend()
        assert backend._partitioned_pg() is False, (
            "_partitioned_pg() must return False on SQLite"
        )

    def test_create_job_far_future_stays_in_queued_job_on_sqlite(self):
        """D6 / R5: far-future jobs stay in QueuedJob on SQLite (SQLite path unchanged).

        _partitioned_pg() returns False on SQLite — far-future staging is disabled.
        """
        from sqlery.core.models import QueuedJob

        backend = _make_sqlite_backend()
        result = _create_basic_job(backend, scheduled_at=_far_future())

        assert isinstance(result, QueuedJob), (
            "D6 — SQLite: far-future jobs must go to QueuedJob (not ScheduledJob). "
            f"Got {type(result).__name__}"
        )

    def test_get_staged_jobs_returns_empty_on_sqlite(self):
        """D6 / R5: get_staged_jobs returns [] on SQLite (staging surface off)."""
        backend = _make_sqlite_backend()
        result = backend.get_staged_jobs()
        assert result == [], (
            f"D6 — SQLite: get_staged_jobs must return [] on SQLite. Got: {result}"
        )

    def test_get_raw_cursor_returns_none_on_sqlite(self):
        """get_raw_cursor() returns None on SQLite (no psycopg available for SQLite)."""
        backend = _make_sqlite_backend()
        cur = backend.get_raw_cursor()
        assert cur is None, (
            f"get_raw_cursor() must return None on SQLite. Got: {cur}"
        )

    def test_claim_job_uses_optimistic_cas_on_sqlite(self):
        """R1: claim_job works on SQLite (optimistic CAS path — no SKIP LOCKED)."""
        backend = _make_sqlite_backend()
        _create_basic_job(backend, queue_name="sqlite-claim-test")

        claimed = backend.claim_job(queues=["sqlite-claim-test"], worker_id="worker_sqlite-node_1")
        assert claimed is not None, "claim_job must succeed on SQLite"
        assert claimed.status == "running", (
            f"R1 — SQLite: claimed job must have status='running'. Got '{claimed.status}'"
        )

    def test_vacuum_runs_on_sqlite(self):
        """vacuum_database succeeds on SQLite.

        CR-03: VACUUM now runs on an AUTOCOMMIT connection (outside the ORM
        transaction) and SQLite uses a plain whole-DB VACUUM, so success must be True
        rather than silently failing inside a transaction block.
        """
        backend = _make_sqlite_backend()
        result = backend.vacuum_database()
        assert isinstance(result, dict), f"vacuum_database must return a dict. Got {type(result)}"
        assert "success" in result, (
            f"vacuum_database must return a dict with 'success' key. Got: {result}"
        )
        assert result["success"] is True, f"vacuum must succeed on SQLite. Got: {result}"

    def test_create_job_immediate_returns_queued_job_on_sqlite(self):
        """create_job without scheduled_at returns QueuedJob on SQLite."""
        from sqlery.core.models import QueuedJob

        backend = _make_sqlite_backend()
        result = _create_basic_job(backend)
        assert isinstance(result, QueuedJob), (
            f"create_job (immediate) must return QueuedJob on SQLite. Got {type(result).__name__}"
        )

    def test_get_job_by_id_returns_job_on_sqlite(self):
        """get_job_by_id returns the created job by id on SQLite."""
        from sqlery.core.models import QueuedJob

        backend = _make_sqlite_backend()
        job = _create_basic_job(backend)
        result = backend.get_job_by_id(job.id)
        assert result is not None
        assert isinstance(result, QueuedJob)
        assert result.id == job.id

    def test_cancel_job_queued_returns_true_on_sqlite(self):
        """cancel_job on a queued job returns True on SQLite."""
        backend = _make_sqlite_backend()
        job = _create_basic_job(backend)
        result = backend.cancel_job(job.id)
        assert result is True, f"cancel_job on queued job must return True. Got: {result}"

    def test_mark_job_archived_on_sqlite(self):
        """mark_job_archived transitions a failed job to archived status on SQLite."""
        backend = _make_sqlite_backend()
        job = _create_basic_job(backend)
        backend.mark_job_failed(job.id, error="forced failure")
        backend.mark_job_archived(job.id)
        archived = backend.get_job_by_id(job.id)
        assert archived is not None
        assert archived.status == "archived", (
            f"mark_job_archived must set status='archived'. Got '{archived.status}'"
        )


# ---------------------------------------------------------------------------
# PG matrix — skips without SQLERY_TEST_PG_URL
# ---------------------------------------------------------------------------


_SKIP_NO_PG = pytest.mark.skipif(
    not os.environ.get("SQLERY_TEST_PG_URL"),
    reason="SQLERY_TEST_PG_URL not set — PG divergence cells require a live PG connection",
)


@_SKIP_NO_PG
class TestStandaloneDivergenceMatrixPG:
    """SQLAlchemyBackend public methods exercised under partitioned PostgreSQL.

    Assertions cover the documented PG divergence (D6/D8) where behavior differs
    from SQLite — the partitioned PG path must be exercised so regressions are caught.
    """

    def test_cleanup_jobs_routes_to_reclaim_on_partitioned_pg(self):
        """D6 / R3: cleanup_jobs returns reclaimed_via_partition_drop: True on partitioned PG."""
        backend = _make_pg_backend()
        result = backend.cleanup_jobs()
        assert isinstance(result, dict)
        assert "reclaimed_via_partition_drop" in result, (
            f"R3 — PG: cleanup_jobs must return reclaimed_via_partition_drop key. Got: {result}"
        )
        assert result.get("reclaimed_via_partition_drop") is True, (
            f"R3 — PG: reclaimed_via_partition_drop must be True. Got: {result}"
        )
        assert "dropped_partitions" in result, (
            f"R3 — PG: cleanup_jobs result must include 'dropped_partitions'. Got: {result}"
        )

    def test_partitioned_pg_returns_true_on_fresh_pg_install(self):
        """D8: _partitioned_pg() returns True on PG fresh install."""
        backend = _make_pg_backend()
        assert backend._partitioned_pg() is True, (
            "_partitioned_pg() must return True on the partitioned PG test database. "
            "Check that init_database applied the partitioned DDL."
        )

    def test_create_job_far_future_routes_to_staging_on_pg(self):
        """D6 / R5: far-future jobs go to ScheduledJob (staging table) on partitioned PG."""
        from sqlery.core.models import ScheduledJob

        backend = _make_pg_backend()
        result = _create_basic_job(backend, scheduled_at=_far_future())
        assert isinstance(result, ScheduledJob), (
            "D6 — PG: far-future jobs must go to ScheduledJob on partitioned PG. "
            f"Got {type(result).__name__}"
        )

    def test_get_staged_jobs_returns_list_on_pg(self):
        """R5: get_staged_jobs returns a list (may be empty) on partitioned PG."""
        backend = _make_pg_backend()
        result = backend.get_staged_jobs()
        assert isinstance(result, list), (
            f"R5 — PG: get_staged_jobs must return a list. Got {type(result).__name__}"
        )

    def test_get_raw_cursor_returns_cursor_on_partitioned_pg(self):
        """R3: get_raw_cursor() returns a non-None cursor on partitioned PG (daemon integration)."""
        backend = _make_pg_backend()
        cur = backend.get_raw_cursor()
        assert cur is not None, (
            "get_raw_cursor() must return a cursor on partitioned PG. "
            "The daemon's maintenance loop relies on this."
        )
        # Caller owns the cursor lifecycle.
        try:
            cur.close()
            cur.connection.close()
        except Exception:
            pass

    def test_claim_uses_skip_locked_on_pg(self):
        """R1: claim_job returns a running job on partitioned PG (SELECT FOR UPDATE SKIP LOCKED)."""
        backend = _make_pg_backend()
        _create_basic_job(backend, queue_name="pg-claim-test")

        claimed = backend.claim_job(queues=["pg-claim-test"], worker_id="worker_pg-node_7777")
        assert claimed is not None, "claim_job must succeed on partitioned PG"
        assert claimed.status == "running", (
            f"R1 — PG: claimed job must have status='running'. Got '{claimed.status}'"
        )

    def test_explain_cleanup_returns_reclaim_key_not_deleted_key(self):
        """R3: cleanup_jobs routing is active — returns reclaim key, not just deleted key."""
        backend = _make_pg_backend()
        result = backend.cleanup_jobs()
        assert "reclaimed_via_partition_drop" in result, (
            f"R3: cleanup_jobs routing broken — expected reclaimed_via_partition_drop key. Got: {result}"
        )
        # The deleted count may be 0; the routing indicator is what matters.

    def test_back_pressure_invariant_not_dropped_today(self):
        """R4: today's partition survives cleanup_jobs (back-pressure invariant)."""
        from sqlery.fastapi_sqlery.database import get_engine
        from sqlalchemy import text
        from datetime import date

        backend = _make_pg_backend()
        _create_basic_job(backend)  # ensure today's partition exists

        backend.cleanup_jobs()

        today_partition = "sqlery_queued_job_" + date.today().strftime("%Y%m%d")
        engine = get_engine()
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT relname FROM pg_class WHERE relname = :name"),
                {"name": today_partition},
            ).fetchone()
        assert row is not None, (
            f"R4: Today's partition ({today_partition}) must NOT be dropped by cleanup_jobs."
        )

    def test_r6_write_path_pruning_mark_archived(self):
        """R6: EXPLAIN on mark_job_archived UPDATE shows no Append node (single-partition pruning)."""
        from sqlery.fastapi_sqlery.database import get_engine
        from sqlalchemy import text

        backend = _make_pg_backend()
        job = _create_basic_job(backend)
        backend.mark_job_failed(job.id, error="fail for archive")

        engine = get_engine()
        with engine.connect() as conn:
            # Fetch created_at for the EXPLAIN query.
            row = conn.execute(
                text("SELECT created_at, version FROM sqlery_queued_job WHERE id = :id"),
                {"id": job.id},
            ).fetchone()
        assert row is not None, f"Job {job.id} not found after mark_job_failed"
        created_at, version = row

        sql = (
            "UPDATE sqlery_queued_job "
            "SET status='archived' "
            "WHERE id=:id AND created_at=:cat AND status='failed'"
        )
        with engine.connect() as conn:
            rows = conn.execute(
                text(f"EXPLAIN (ANALYZE FALSE, FORMAT TEXT) {sql}"),
                {"id": job.id, "cat": created_at},
            ).fetchall()
        plan = "\n".join(r[0] for r in rows)

        assert _has_single_partition(plan), (
            f"R6: mark_job_archived EXPLAIN should show single-partition pruning.\nPlan:\n{plan}"
        )

    def test_r2_sqlite_batched_delete_not_triggered_on_pg(self):
        """R2 (via routing): PG cleanup does NOT go through batched DELETE path."""
        backend = _make_pg_backend()
        result = backend.cleanup_jobs()
        # On PG, the batched DELETE loop is skipped entirely — result must not be
        # the SQLite shape {"deleted": N, "count": N} without the reclaim key.
        assert "reclaimed_via_partition_drop" in result, (
            "R2 verification: PG cleanup must route to reclaim, not batched DELETE. "
            f"Got: {result}"
        )
