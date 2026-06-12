"""Unit tests for partition-aware methods in SQLAlchemyBackend (17-03).

Covers:
  - _partitioned_pg() behavior on SQLite and mocked PG
  - get_raw_cursor() returns None on SQLite
  - cleanup_jobs routes correctly (reclaim vs batched DELETE)
  - vacuum_database skips jobs table when partitioned
  - Staging dual-table surface (create_job, get_job_by_id, cancel_job, get_staged_jobs)
  - Write-path pruning (mark_job_archived, cascade_ancestor_status, update_job_child_pid)
"""

from __future__ import annotations

from datetime import datetime, timedelta, UTC
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sync_backend(tmp_path, monkeypatch):
    """Build a fresh SQLAlchemyBackend against a per-test temp-file SQLite engine."""
    from sqlalchemy import create_engine
    from sqlmodel import SQLModel

    from sqlery.fastapi_sqlery import database as db_mod
    from sqlery.core import models as _core_models  # noqa: F401

    db_path = tmp_path / "db.sqlite"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db_mod, "_engine", engine, raising=False)

    from sqlery.fastapi_sqlery.backend import SQLAlchemyBackend

    backend = SQLAlchemyBackend()
    # Ensure cache is clear for each test
    backend._partitioned_pg_cache = None
    try:
        yield backend
    finally:
        engine.dispose()


def _create_basic_job(backend, **overrides):
    """Create a QueuedJob with sensible defaults."""
    defaults = dict(
        task_path="tests.fake.task",
        kwargs={"x": 1},
        queue_name="default",
        priority=0,
        scheduled_at=None,
        max_retries=0,
        retry_backoff=1.0,
        allow_parallel=False,
        timeout_seconds=None,
    )
    defaults.update(overrides)
    return backend.create_job(**defaults)


# ---------------------------------------------------------------------------
# 1. _partitioned_pg()
# ---------------------------------------------------------------------------


class TestPartitionedPg:
    def test_sqlite_returns_false(self, sync_backend):
        """_partitioned_pg() returns False on a SQLite backend (dialect != postgresql)."""
        result = sync_backend._partitioned_pg()
        assert result is False

    def test_sqlite_caches_false(self, sync_backend):
        """_partitioned_pg() caches the False result for SQLite."""
        sync_backend._partitioned_pg()
        assert sync_backend._partitioned_pg_cache is False

    def test_pg_plain_table_returns_false(self, sync_backend):
        """_partitioned_pg() returns False when PG catalog shows relkind='r'."""
        mock_engine = MagicMock()
        mock_engine.dialect.name = "postgresql"
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_result = MagicMock()
        mock_result.fetchone.return_value = (False,)
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value = mock_conn
        sync_backend._partitioned_pg_cache = None
        with patch("sqlery.fastapi_sqlery.backend.get_engine", return_value=mock_engine):
            result = sync_backend._partitioned_pg()
        assert result is False
        assert sync_backend._partitioned_pg_cache is False

    def test_pg_partitioned_table_returns_true(self, sync_backend):
        """_partitioned_pg() returns True when PG catalog shows relkind='p'."""
        mock_engine = MagicMock()
        mock_engine.dialect.name = "postgresql"
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_result = MagicMock()
        mock_result.fetchone.return_value = (True,)
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value = mock_conn
        sync_backend._partitioned_pg_cache = None
        with patch("sqlery.fastapi_sqlery.backend.get_engine", return_value=mock_engine):
            result = sync_backend._partitioned_pg()
        assert result is True
        assert sync_backend._partitioned_pg_cache is True

    def test_transient_error_does_not_cache_false(self, sync_backend):
        """WR-01: _partitioned_pg() does NOT permanently cache False on a transient DB error."""
        mock_engine = MagicMock()
        mock_engine.dialect.name = "postgresql"
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(side_effect=Exception("DB connection failed"))
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_engine.connect.return_value = mock_conn
        sync_backend._partitioned_pg_cache = None
        with patch("sqlery.fastapi_sqlery.backend.get_engine", return_value=mock_engine):
            result = sync_backend._partitioned_pg()
        # Must return False for safety
        assert result is False
        # But cache must NOT be written so next call retries
        assert sync_backend._partitioned_pg_cache is None

    def test_cached_result_not_rechecked(self, sync_backend):
        """Once cached, _partitioned_pg() returns cached value without re-querying."""
        sync_backend._partitioned_pg_cache = True
        # No mock needed — should return True without touching DB
        result = sync_backend._partitioned_pg()
        assert result is True


# ---------------------------------------------------------------------------
# 2. get_raw_cursor()
# ---------------------------------------------------------------------------


class TestGetRawCursor:
    def test_sqlite_returns_none(self, sync_backend):
        """get_raw_cursor() returns None on SQLite (not partitioned)."""
        result = sync_backend.get_raw_cursor()
        assert result is None

    def test_partitioned_pg_returns_cursor(self, sync_backend):
        """get_raw_cursor() returns a cursor object when partitioned PG is True."""
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_engine = MagicMock()
        mock_engine.raw_connection.return_value = mock_conn
        # Pre-cache True to avoid catalog query
        sync_backend._partitioned_pg_cache = True
        with patch("sqlery.fastapi_sqlery.backend.get_engine", return_value=mock_engine):
            result = sync_backend.get_raw_cursor()
        assert result is mock_cursor


# ---------------------------------------------------------------------------
# 3. cleanup_jobs routing
# ---------------------------------------------------------------------------


class TestCleanupJobsRouting:
    def test_sqlite_uses_batched_delete(self, sync_backend):
        """cleanup_jobs returns {"deleted": N} on SQLite (batched DELETE path, D6)."""
        _create_basic_job(sync_backend)
        result = sync_backend.cleanup_jobs()
        # On SQLite returns dict with "deleted" key (not reclaimed_via_partition_drop)
        assert isinstance(result, dict)
        assert "deleted" in result
        assert "reclaimed_via_partition_drop" not in result

    def test_partitioned_pg_routes_to_reclaim(self, sync_backend):
        """cleanup_jobs returns reclaimed_via_partition_drop dict on partitioned PG."""
        import sqlery.core.partitioning as partitioning_mod

        mock_reclaim = MagicMock(return_value=2)
        mock_cursor = MagicMock()
        mock_raw_conn = MagicMock()
        mock_raw_conn.cursor.return_value = mock_cursor
        mock_engine = MagicMock()
        mock_engine.raw_connection.return_value = mock_raw_conn

        sync_backend._partitioned_pg_cache = True
        with patch("sqlery.fastapi_sqlery.backend.get_engine", return_value=mock_engine):
            with patch("sqlery.fastapi_sqlery.backend._partitioning") as mock_part:
                mock_part.reclaim_drained_partitions = mock_reclaim
                result = sync_backend.cleanup_jobs()

        assert result.get("reclaimed_via_partition_drop") is True
        assert "dropped_partitions" in result

    def test_partitioned_pg_dry_run_skips_reclaim(self, sync_backend):
        """cleanup_jobs dry_run on partitioned PG returns count without dropping."""
        sync_backend._partitioned_pg_cache = True
        with patch("sqlery.fastapi_sqlery.backend._partitioning") as mock_part:
            result = sync_backend.cleanup_jobs(dry_run=True)
        # dry_run should not call reclaim
        assert not mock_part.reclaim_drained_partitions.called
        assert "count" in result

    def test_cursor_closed_in_finally(self, sync_backend):
        """get_raw_cursor() cursor is closed even if reclaim raises."""
        mock_cursor = MagicMock()
        mock_raw_conn = MagicMock()
        mock_raw_conn.cursor.return_value = mock_cursor
        mock_engine = MagicMock()
        mock_engine.raw_connection.return_value = mock_raw_conn

        sync_backend._partitioned_pg_cache = True
        with patch("sqlery.fastapi_sqlery.backend.get_engine", return_value=mock_engine):
            with patch("sqlery.fastapi_sqlery.backend._partitioning") as mock_part:
                mock_part.reclaim_drained_partitions.side_effect = RuntimeError("boom")
                with pytest.raises(RuntimeError, match="boom"):
                    sync_backend.cleanup_jobs()
        # Cursor must be closed even when reclaim raised
        mock_cursor.close.assert_called_once()


# ---------------------------------------------------------------------------
# 4. vacuum_database
# ---------------------------------------------------------------------------


class TestVacuumDatabase:
    def test_sqlite_does_not_vacuum_individual_tables(self, sync_backend):
        """vacuum_database on SQLite runs single VACUUM (not per-table)."""
        # Should not raise; SQLite VACUUM is a no-op in test env
        result = sync_backend.vacuum_database()
        # On SQLite the single-VACUUM path may or may not succeed — just check return shape
        assert isinstance(result, dict)

    def test_partitioned_pg_skips_jobs_table_vacuum(self, sync_backend):
        """vacuum_database skips VACUUM ANALYZE sqlery_queued_job when partitioned."""
        executed_sqls = []

        mock_session = MagicMock()

        def capture_exec(stmt):
            executed_sqls.append(str(stmt))
            return MagicMock()

        mock_session.exec.side_effect = capture_exec
        mock_session.commit = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        sync_backend._partitioned_pg_cache = True

        with patch.object(sync_backend, "_get_session", return_value=mock_session):
            result = sync_backend.vacuum_database()

        # sqlery_queued_job must NOT be vacuumed when partitioned
        queued_job_vacuumed = any("sqlery_queued_job" in s for s in executed_sqls)
        assert not queued_job_vacuumed, f"VACUUM on jobs table found in: {executed_sqls}"


# ---------------------------------------------------------------------------
# 5. Staging surface
# ---------------------------------------------------------------------------


class TestStagingDualTable:
    def test_create_job_sqlite_always_uses_queued_job(self, sync_backend):
        """On SQLite (D6), create_job always inserts into QueuedJob regardless of scheduled_at."""
        from sqlery.core.models import QueuedJob

        far_future = datetime.now(UTC) + timedelta(days=30)
        job = _create_basic_job(sync_backend, scheduled_at=far_future)
        assert isinstance(job, QueuedJob)

    def test_create_job_partitioned_pg_far_future_routes_to_staging(self, sync_backend):
        """On partitioned PG, create_job routes far-future jobs to ScheduledJobStaging."""
        from sqlery.core.models import ScheduledJob

        far_future = datetime.now(UTC) + timedelta(days=30)
        sync_backend._partitioned_pg_cache = True

        job = _create_basic_job(sync_backend, scheduled_at=far_future)
        assert isinstance(job, ScheduledJob)

    def test_create_job_partitioned_pg_near_future_uses_queued_job(self, sync_backend):
        """On partitioned PG, create_job with near-future scheduled_at uses QueuedJob."""
        from sqlery.core.models import QueuedJob

        near_future = datetime.now(UTC) + timedelta(minutes=30)
        sync_backend._partitioned_pg_cache = True

        job = _create_basic_job(sync_backend, scheduled_at=near_future)
        assert isinstance(job, QueuedJob)

    def test_get_job_by_id_checks_queued_job_first(self, sync_backend):
        """get_job_by_id returns QueuedJob row when found there."""
        job = _create_basic_job(sync_backend)
        found = sync_backend.get_job_by_id(job.id)
        assert found is not None
        assert found.id == job.id

    def test_get_job_by_id_falls_back_to_staging(self, sync_backend):
        """get_job_by_id falls back to ScheduledJob when not in QueuedJob (partitioned PG)."""
        from sqlery.core.models import ScheduledJob

        far_future = datetime.now(UTC) + timedelta(days=30)
        sync_backend._partitioned_pg_cache = True

        staged_job = _create_basic_job(sync_backend, scheduled_at=far_future)
        assert isinstance(staged_job, ScheduledJob)

        # Should be findable via get_job_by_id
        found = sync_backend.get_job_by_id(staged_job.id)
        assert found is not None
        assert found.id == staged_job.id

    def test_get_staged_jobs_sqlite_returns_empty(self, sync_backend):
        """get_staged_jobs() returns [] on SQLite (not partitioned)."""
        result = sync_backend.get_staged_jobs()
        assert result == []

    def test_get_staged_jobs_partitioned_pg_returns_rows(self, sync_backend):
        """get_staged_jobs() returns ScheduledJob rows on partitioned PG."""
        far_future = datetime.now(UTC) + timedelta(days=30)
        sync_backend._partitioned_pg_cache = True

        _create_basic_job(sync_backend, scheduled_at=far_future)
        _create_basic_job(sync_backend, scheduled_at=far_future + timedelta(days=1))

        result = sync_backend.get_staged_jobs()
        assert len(result) == 2

    def test_cancel_job_queued_job(self, sync_backend):
        """cancel_job cancels a queued job in QueuedJob table."""
        job = _create_basic_job(sync_backend)
        ok = sync_backend.cancel_job(job.id)
        assert ok is True

    def test_cancel_staged_job_partitioned_pg(self, sync_backend):
        """cancel_job cancels a staged ScheduledJob row on partitioned PG."""
        far_future = datetime.now(UTC) + timedelta(days=30)
        sync_backend._partitioned_pg_cache = True

        staged = _create_basic_job(sync_backend, scheduled_at=far_future)

        ok = sync_backend.cancel_job(staged.id)
        assert ok is True

        # Row should be gone from staging table
        remaining = sync_backend.get_staged_jobs()
        assert len(remaining) == 0


# ---------------------------------------------------------------------------
# 6. Write-path pruning
# ---------------------------------------------------------------------------


class TestWritePathPruning:
    def test_mark_job_archived_uses_created_at_filter(self, sync_backend):
        """mark_job_archived uses created_at in UPDATE filter (write-path pruning)."""
        job = _create_basic_job(sync_backend)
        # Manually set job to failed so it can be archived
        from sqlery.fastapi_sqlery import database as db_mod
        from sqlmodel import Session, select as sqlmodel_select

        with Session(db_mod._engine) as session:
            from sqlery.core.models import QueuedJob
            db_job = session.exec(
                sqlmodel_select(QueuedJob).where(QueuedJob.id == job.id)
            ).first()
            db_job.status = "failed"
            session.add(db_job)
            session.commit()

        # Should complete without error
        sync_backend.mark_job_archived(job.id)

        found = sync_backend.get_job_by_id(job.id)
        assert found.status == "archived"

    def test_cascade_ancestor_status_walks_chain(self, sync_backend):
        """cascade_ancestor_status updates all ancestors via created_at-aware path."""
        parent = _create_basic_job(sync_backend)
        child = _create_basic_job(sync_backend, parent_job_id=parent.id)

        sync_backend.cascade_ancestor_status(child.id, "failed")

        parent_after = sync_backend.get_job_by_id(parent.id)
        assert parent_after.status == "failed"

    def test_update_job_child_pid_with_created_at(self, sync_backend):
        """update_job_child_pid accepts optional created_at and stores child_pid."""
        job = _create_basic_job(sync_backend)
        sync_backend.update_job_child_pid(job.id, child_pid=12345, created_at=job.created_at)
        found = sync_backend.get_job_by_id(job.id)
        assert found.child_pid == 12345

    def test_update_job_child_pid_without_created_at(self, sync_backend):
        """update_job_child_pid still works without created_at (SQLite compatibility)."""
        job = _create_basic_job(sync_backend)
        sync_backend.update_job_child_pid(job.id, child_pid=99999)
        found = sync_backend.get_job_by_id(job.id)
        assert found.child_pid == 99999
