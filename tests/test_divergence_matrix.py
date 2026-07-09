"""SQLite × PostgreSQL divergence matrix for DjangoBackend.

Phase 16, plan 16-04: Acceptance criterion SC-3.
Exercises representative DjangoBackend methods under both SQLite and PG,
asserting consistent return types and shapes, with documented divergences:

  D6: cleanup_jobs behavior
    - SQLite: returns {"deleted": N, "count": N}  (batched DELETE path)
    - PG (partitioned): returns {"reclaimed_via_partition_drop": True, "dropped_partitions": N, ...}
  D6: create_job far-future routing
    - SQLite: far-future jobs go to QueuedJob (unchanged)
    - PG (partitioned): far-future jobs go to ScheduledJob

TestDivergenceMatrixSQLite always runs (no PG URL needed).
TestDivergenceMatrixPG skips when SQLERY_TEST_PG_URL is not set.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_backend():
    from sqlery.django_sqlery.backend import DjangoBackend
    return DjangoBackend()


def _create_basic_job(backend, scheduled_at=None, queue_name="default"):
    return backend.create_job(
        task_path="tests.divergence.noop",
        kwargs={"k": 1},
        queue_name=queue_name,
        priority=0,
        scheduled_at=scheduled_at,
        max_retries=0,
        retry_backoff=1.0,
        allow_parallel=True,
        timeout_seconds=None,
    )


def _far_future():
    return datetime.now(timezone.utc) + timedelta(days=60)


# ---------------------------------------------------------------------------
# SQLite matrix — always runs
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDivergenceMatrixSQLite:
    """DjangoBackend public methods exercised under SQLite.

    Assertions cover the documented SQLite behavior (D6) that must remain
    byte-for-byte unchanged regardless of Phase 16 changes.
    """

    def test_create_job_returns_queued_job(self):
        """create_job returns QueuedJob instance on SQLite."""
        from sqlery.django_sqlery.models import QueuedJob

        backend = _make_backend()
        result = _create_basic_job(backend)
        assert isinstance(result, QueuedJob), (
            f"create_job must return QueuedJob on SQLite, got {type(result).__name__}"
        )

    def test_create_job_far_future_stays_in_queued_job(self):
        """D6 / R10: far-future jobs stay in QueuedJob on SQLite (SQLite path unchanged).

        _partitioned_pg() returns False on SQLite — far-future staging is disabled.
        This verifies the Phase 16 carry-forward: SQLite behavior is byte-for-byte unchanged.

        Skips when SQLERY_TEST_PG_URL is set (test DB is then PG, not SQLite).
        """
        from django.db import connection as conn
        if conn.vendor != "sqlite":
            pytest.skip("SQLite-specific test; skipping when test DB is not SQLite")

        from sqlery.django_sqlery.models import QueuedJob, ScheduledJob

        backend = _make_backend()

        result = _create_basic_job(backend, scheduled_at=_far_future())
        # D6: On SQLite, far-future jobs are NOT routed to ScheduledJob
        assert isinstance(result, QueuedJob), (
            "D6 — SQLite: far-future jobs must go to QueuedJob (not ScheduledJob). "
            f"Got {type(result).__name__}"
        )
        assert not isinstance(result, ScheduledJob), (
            "D6 — SQLite: ScheduledJob routing must NOT occur when _partitioned_pg() is False."
        )

    def test_get_job_by_id_returns_queued_job(self):
        """get_job_by_id returns the QueuedJob by id on SQLite."""
        from sqlery.django_sqlery.models import QueuedJob

        backend = _make_backend()
        job = _create_basic_job(backend)
        result = backend.get_job_by_id(job.id)
        assert result is not None
        assert isinstance(result, QueuedJob)
        assert result.id == job.id

    def test_get_job_by_id_returns_none_for_missing(self):
        """get_job_by_id returns None for a non-existent id."""
        backend = _make_backend()
        assert backend.get_job_by_id(9999999) is None

    def test_cancel_job_queued_returns_true(self):
        """cancel_job on a queued job returns True on SQLite."""
        backend = _make_backend()
        job = _create_basic_job(backend)
        result = backend.cancel_job(job.id)
        assert result is True
        job.refresh_from_db()
        assert job.status == "failed"

    def test_cancel_job_missing_returns_false(self):
        """cancel_job on a non-existent job returns False."""
        backend = _make_backend()
        assert backend.cancel_job(9999999) is False

    def test_cleanup_jobs_returns_deleted_key(self):
        """D6: cleanup_jobs on SQLite returns a dict with 'deleted' key (batched DELETE path).

        Skips when SQLERY_TEST_PG_URL is set (test DB is then PG, not SQLite).
        """
        from django.db import connection as conn
        if conn.vendor != "sqlite":
            pytest.skip("SQLite-specific test; skipping when test DB is not SQLite")

        backend = _make_backend()
        result = backend.cleanup_jobs()
        assert isinstance(result, dict)
        assert "deleted" in result, (
            f"D6 — SQLite: cleanup_jobs must return dict with 'deleted' key. Got: {result}"
        )
        # Must NOT have PG-specific key on SQLite
        assert "reclaimed_via_partition_drop" not in result, (
            "D6 — SQLite: cleanup_jobs must NOT return 'reclaimed_via_partition_drop' on SQLite. "
            f"Got: {result}"
        )

    def test_vacuum_database_returns_dict(self):
        """vacuum_database returns a dict with a 'success' key on SQLite.

        Note: In a Django test transaction, SQLite VACUUM raises 'cannot VACUUM
        from within a transaction', so success may be False in this context.
        The important assertion is that the method returns a dict with a 'success'
        key rather than raising an unhandled exception.
        """
        backend = _make_backend()
        result = backend.vacuum_database()
        assert isinstance(result, dict)
        assert "success" in result, (
            f"vacuum_database must return a dict with 'success' key. Got: {result}"
        )

    def test_get_jobs_returns_list(self):
        """get_jobs returns a list of QueuedJob instances on SQLite."""
        from sqlery.django_sqlery.models import QueuedJob

        backend = _make_backend()
        _create_basic_job(backend)
        result = backend.get_jobs()
        assert isinstance(result, list)
        assert len(result) >= 1
        for row in result:
            assert isinstance(row, QueuedJob)

    def test_mark_job_archived_works(self):
        """mark_job_archived transitions a failed job to archived status."""
        from sqlery.django_sqlery.models import QueuedJob

        backend = _make_backend()
        job = _create_basic_job(backend)
        QueuedJob.objects.filter(id=job.id).update(status="failed")
        backend.mark_job_archived(job.id)
        job.refresh_from_db()
        assert job.status == "archived"

    def test_partitioned_pg_is_false_on_sqlite(self):
        """_partitioned_pg() returns False on SQLite (D6 gate — SQLite path unchanged).

        Skips when SQLERY_TEST_PG_URL is set (test DB is then PG, not SQLite).
        """
        from django.db import connection

        if connection.vendor != "sqlite":
            pytest.skip("SQLite-specific test; skipping when test DB is not SQLite")

        backend = _make_backend()
        assert backend._partitioned_pg() is False, (
            "_partitioned_pg() must return False on SQLite"
        )


# ---------------------------------------------------------------------------
# PG matrix — skips without SQLERY_TEST_PG_URL
# ---------------------------------------------------------------------------


_SKIP_NO_PG = pytest.mark.skipif(
    not os.environ.get("SQLERY_TEST_PG_URL"),
    reason="SQLERY_TEST_PG_URL not set — PG divergence cells require a live PG connection",
)


@pytest.mark.django_db
@_SKIP_NO_PG
class TestDivergenceMatrixPG:
    """DjangoBackend public methods exercised under partitioned PostgreSQL.

    Assertions cover the documented PG divergence (D6) where behavior differs
    from SQLite — the partitioned PG path must be exercised so regressions are caught.
    """

    def test_partitioned_pg_is_true_on_pg(self):
        """_partitioned_pg() returns True on the partitioned PG test database."""
        from django.db import connection

        assert connection.vendor == "postgresql", (
            "TestDivergenceMatrixPG must run against PostgreSQL"
        )
        backend = _make_backend()
        assert backend._partitioned_pg() is True, (
            "_partitioned_pg() must return True on the partitioned PG test database. "
            "Check that migration 0030 has been applied."
        )

    def test_create_job_immediate_returns_queued_job(self):
        """create_job with no scheduled_at returns QueuedJob on PG (same as SQLite)."""
        from sqlery.django_sqlery.models import QueuedJob

        backend = _make_backend()
        result = _create_basic_job(backend)
        assert isinstance(result, QueuedJob), (
            f"create_job (immediate) must return QueuedJob on PG. Got {type(result).__name__}"
        )

    def test_create_job_far_future_goes_to_scheduled_job(self):
        """D6 — PG: far-future jobs go to ScheduledJob (partition-aware routing)."""
        from sqlery.django_sqlery.models import ScheduledJob

        backend = _make_backend()
        result = _create_basic_job(backend, scheduled_at=_far_future())
        assert isinstance(result, ScheduledJob), (
            "D6 — PG: far-future jobs must go to ScheduledJob on partitioned PG. "
            f"Got {type(result).__name__}"
        )

    def test_cleanup_jobs_returns_reclaimed_via_partition_drop(self):
        """D6 — PG: cleanup_jobs returns reclaimed_via_partition_drop: True on PG."""
        backend = _make_backend()
        result = backend.cleanup_jobs()
        assert isinstance(result, dict)
        assert result.get("reclaimed_via_partition_drop") is True, (
            f"D6 — PG: cleanup_jobs must return reclaimed_via_partition_drop: True. Got: {result}"
        )
        assert "dropped_partitions" in result, (
            f"D6 — PG: cleanup_jobs result must include 'dropped_partitions'. Got: {result}"
        )
        # Must NOT have SQLite-specific key on PG
        assert result.get("deleted") == 0, (
            f"D6 — PG: cleanup_jobs result 'deleted' must be 0 (partition path). Got: {result}"
        )

    def test_vacuum_database_returns_dict_on_pg(self):
        """vacuum_database returns a dict with 'success' key on PG.

        Note: Django test databases wrap operations in a transaction, so VACUUM
        raises 'cannot run inside a transaction block' — success may be False here.
        The important assertion is the method returns a structured dict rather than
        raising an unhandled exception.
        """
        backend = _make_backend()
        result = backend.vacuum_database()
        assert isinstance(result, dict)
        assert "success" in result, (
            f"vacuum_database must return a dict with 'success' key on PG. Got: {result}"
        )

    def test_get_job_by_id_returns_queued_job_on_pg(self):
        """get_job_by_id returns QueuedJob on PG (same as SQLite)."""
        from sqlery.django_sqlery.models import QueuedJob

        backend = _make_backend()
        job = _create_basic_job(backend)
        result = backend.get_job_by_id(job.id)
        assert result is not None
        assert isinstance(result, QueuedJob)
        assert result.id == job.id

    def test_cancel_job_returns_true_on_pg(self):
        """cancel_job on queued job returns True on PG (same as SQLite)."""
        backend = _make_backend()
        job = _create_basic_job(backend)
        assert backend.cancel_job(job.id) is True
        job.refresh_from_db()
        assert job.status == "failed"

    def test_get_raw_cursor_returns_cursor_on_pg(self):
        """get_raw_cursor() returns a non-None cursor on partitioned PG."""
        backend = _make_backend()
        cursor = backend.get_raw_cursor()
        assert cursor is not None, (
            "get_raw_cursor() must return a cursor on partitioned PG. "
            "The daemon's maintenance loop relies on this."
        )
        cursor.close()
