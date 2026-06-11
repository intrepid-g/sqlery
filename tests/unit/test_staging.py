"""Unit tests for Phase 14 scheduled-job staging.

Tests cover all three success criteria:
  SC-1 (far-future routing + dual-table visibility),
  SC-2 (exactly-once promotion with mock cursor),
  SC-3 (config validation).
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, call


# ---------------------------------------------------------------------------
# Mock cursor factory — mirrors the pattern in tests/unit/test_partitioning.py
# ---------------------------------------------------------------------------


def _make_cursor(fetchall_sequence=None, fetchone_sequence=None):
    """Return a MagicMock cursor whose fetchall()/fetchone() consume sequences.

    Args:
        fetchall_sequence: list of lists; each call to fetchall() pops the next.
        fetchone_sequence: list of tuples; each call to fetchone() pops the next.
    """
    cur = MagicMock()
    _fetchall_itr = iter(fetchall_sequence or [])
    _fetchone_itr = iter(fetchone_sequence or [])

    cur.fetchall.side_effect = lambda: next(_fetchall_itr)
    cur.fetchone.side_effect = lambda: next(_fetchone_itr)
    return cur


# ---------------------------------------------------------------------------
# SC-1: Far-future job routing + dual-table visibility
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStagingRouting:
    """SC-1: A job scheduled 60 days out is in ScheduledJob (not QueuedJob),
    visible to get_job_by_id and cancel_job."""

    @staticmethod
    def _make_backend():
        from sqlery.django_sqlery.backend import DjangoBackend
        return DjangoBackend()

    @staticmethod
    def _create_far_future_job(backend):
        """Enqueue a job with scheduled_at 60 days out."""
        return backend.create_job(
            task_path="tests.fake.staging_task",
            kwargs={"x": 42},
            queue_name="default",
            priority=0,
            scheduled_at=datetime.now(timezone.utc) + timedelta(days=60),
            max_retries=0,
            retry_backoff=1.0,
            allow_parallel=False,
            timeout_seconds=None,
        )

    def test_far_future_job_goes_to_staging(self):
        """A job 60 days out is routed to ScheduledJob, not QueuedJob."""
        from sqlery.django_sqlery.models import ScheduledJob
        backend = self._make_backend()
        result = self._create_far_future_job(backend)
        assert isinstance(result, ScheduledJob), (
            f"Expected ScheduledJob, got {type(result).__name__}"
        )

    def test_far_future_job_invisible_to_claim_queue(self):
        """A staged job never appears in sqlery_queued_job (cannot be claimed)."""
        from sqlery.django_sqlery.models import QueuedJob
        backend = self._make_backend()
        job = self._create_far_future_job(backend)
        assert not QueuedJob.objects.filter(id=job.id).exists()

    def test_far_future_job_visible_to_get_job_by_id(self):
        """get_job_by_id returns the staged job — status API can find it."""
        from sqlery.django_sqlery.models import ScheduledJob
        backend = self._make_backend()
        job = self._create_far_future_job(backend)
        result = backend.get_job_by_id(job.id)
        assert result is not None
        assert isinstance(result, ScheduledJob)
        assert result.id == job.id

    def test_far_future_job_cancellable(self):
        """cancel_job returns True and removes the staged job — cancel API works."""
        from sqlery.django_sqlery.models import ScheduledJob
        backend = self._make_backend()
        job = self._create_far_future_job(backend)
        job_id = job.id
        assert backend.cancel_job(job_id) is True
        assert not ScheduledJob.objects.filter(id=job_id).exists()

    def test_near_future_job_goes_to_queued_job(self):
        """A job 12 hours out is below the threshold — goes to QueuedJob."""
        from sqlery.django_sqlery.models import QueuedJob
        backend = self._make_backend()
        result = backend.create_job(
            task_path="tests.fake.staging_task",
            kwargs={},
            queue_name="default",
            priority=0,
            scheduled_at=datetime.now(timezone.utc) + timedelta(hours=12),
            max_retries=0,
            retry_backoff=1.0,
            allow_parallel=False,
            timeout_seconds=None,
        )
        assert isinstance(result, QueuedJob), (
            f"Expected QueuedJob for near-future job, got {type(result).__name__}"
        )


# ---------------------------------------------------------------------------
# SC-2: Exactly-once promotion — advisory lock + SKIP LOCKED (mock cursor)
# ---------------------------------------------------------------------------


class TestPromotion:
    """SC-2: Two concurrent promoters never double-promote.

    All tests use a mock cursor — no live DB or psycopg connection required.
    The advisory lock + SKIP LOCKED semantics are tested by observing what
    SQL execute() calls are made against the mock cursor.
    """

    def test_skips_when_lock_not_acquired(self):
        """If pg_try_advisory_lock returns False, return 0 and issue no DELETE."""
        from sqlery.core.scheduler import promote_due_scheduled_jobs, ADVISORY_LOCK_PROMOTE

        cur = _make_cursor(fetchone_sequence=[(False,)])
        result = promote_due_scheduled_jobs(cur)
        assert result == 0
        # Only the lock-attempt execute should have been called; no DELETE
        calls = [str(c) for c in cur.execute.call_args_list]
        assert not any("DELETE" in c for c in calls), (
            "No DELETE should be issued when lock is not acquired"
        )

    def test_promotes_rows_when_lock_acquired(self):
        """With 2 rows returned from DELETE RETURNING, returns 2; unlock is called."""
        from sqlery.core.scheduler import promote_due_scheduled_jobs, ADVISORY_LOCK_PROMOTE

        now = datetime.now(timezone.utc)
        rows = [
            (101, "default", "m.f", {"a": 1}, now, 0, 0, now),
            (102, "default", "m.g", {"b": 2}, now, 0, 0, now),
        ]
        cur = _make_cursor(
            fetchone_sequence=[(True,)],
            fetchall_sequence=[rows],
        )
        result = promote_due_scheduled_jobs(cur)
        assert result == 2
        # Unlock must have been called in the finally block
        unlock_calls = [
            c for c in cur.execute.call_args_list
            if "pg_advisory_unlock" in str(c)
        ]
        assert len(unlock_calls) == 1, "advisory_unlock must be called exactly once"

    def test_returns_zero_when_no_due_rows(self):
        """Lock acquired but DELETE RETURNING yields no rows -> returns 0."""
        from sqlery.core.scheduler import promote_due_scheduled_jobs, ADVISORY_LOCK_PROMOTE

        cur = _make_cursor(
            fetchone_sequence=[(True,)],
            fetchall_sequence=[[]],
        )
        result = promote_due_scheduled_jobs(cur)
        assert result == 0

    def test_advisory_unlock_called_even_on_insert_error(self):
        """Lock acquired; INSERT raises mid-loop; advisory_unlock still called (finally)."""
        from sqlery.core.scheduler import promote_due_scheduled_jobs, ADVISORY_LOCK_PROMOTE

        now = datetime.now(timezone.utc)
        rows = [(101, "default", "m.f", {}, now, 0, 0, now)]

        call_count = [0]
        original_execute = MagicMock()

        def execute_side_effect(sql, params=None):
            call_count[0] += 1
            # First call: pg_try_advisory_lock — succeeds
            # Second call: DELETE RETURNING — succeeds (fetchall returns rows)
            # Third call: INSERT — raises RuntimeError
            # Fourth call: pg_advisory_unlock — must still happen
            if call_count[0] == 3 and "INSERT" in str(sql):
                raise RuntimeError("simulated INSERT failure")

        cur = _make_cursor(
            fetchone_sequence=[(True,)],
            fetchall_sequence=[rows],
        )
        cur.execute.side_effect = execute_side_effect

        with pytest.raises(RuntimeError, match="simulated INSERT failure"):
            promote_due_scheduled_jobs(cur)

        # advisory_unlock must have been called even after the exception
        unlock_calls = [
            c for c in cur.execute.call_args_list
            if "pg_advisory_unlock" in str(c)
        ]
        assert len(unlock_calls) == 1, (
            "advisory_unlock must be called in finally, even when INSERT raises"
        )


# ---------------------------------------------------------------------------
# SC-3: Config validation rejects retention <= threshold
# ---------------------------------------------------------------------------


class TestStagingConfigValidation:
    """SC-3: _validate_staging_config raises ValueError when retention <= threshold."""

    def test_valid_config_does_not_raise(self):
        """threshold=1, retention='30 days' is valid — no exception."""
        from sqlery.core.daemon import _validate_staging_config
        # Should not raise
        _validate_staging_config(threshold_days=1, retention_str="30 days")

    def test_equal_retention_and_threshold_raises(self):
        """threshold=30, retention='30 days' — equal case raises ValueError."""
        from sqlery.core.daemon import _validate_staging_config
        with pytest.raises(ValueError, match="must be greater than"):
            _validate_staging_config(threshold_days=30, retention_str="30 days")

    def test_retention_less_than_threshold_raises(self):
        """threshold=31, retention='30 days' — retention < threshold raises ValueError."""
        from sqlery.core.daemon import _validate_staging_config
        with pytest.raises(ValueError):
            _validate_staging_config(threshold_days=31, retention_str="30 days")

    def test_exactly_equal_raises(self):
        """threshold=1, retention='1 day' — equal case raises ValueError."""
        from sqlery.core.daemon import _validate_staging_config
        with pytest.raises(ValueError):
            _validate_staging_config(threshold_days=1, retention_str="1 day")
