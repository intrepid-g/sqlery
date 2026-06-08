"""Tests for cron drift correction, future-clamp, and bounded jitter (Phase 10 Plan 03).

These are framework-agnostic unit tests that do not require a database:
- calculate_next_run drift correction + future clamp (Task 1)
- _enqueue_for_scheduled_task atomic advance + bounded jitter wiring (Task 2)
"""

from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlery.core.scheduler import Scheduler


def _bare_scheduler() -> Scheduler:
    """Build a Scheduler without invoking get_backend()."""
    s = Scheduler.__new__(Scheduler)
    s.backend = MagicMock()
    return s


class TestCalculateNextRunDriftClamp:
    """Task 1: drift correction from scheduled time + future clamp."""

    def test_far_past_base_time_clamps_to_future(self):
        s = _bare_scheduler()
        now = datetime.now(timezone.utc)
        stale = now - timedelta(days=1)
        nxt = s.calculate_next_run("* * * * *", base_time=stale)
        assert nxt > now, (nxt, now)

    def test_near_now_base_time_returns_next_future(self):
        s = _bare_scheduler()
        now = datetime.now(timezone.utc)
        nxt = s.calculate_next_run("* * * * *", base_time=now)
        assert nxt > now, (nxt, now)

    def test_base_time_none_defaults_to_now(self):
        s = _bare_scheduler()
        now = datetime.now(timezone.utc)
        nxt = s.calculate_next_run("* * * * *")
        assert nxt > now

    def test_naive_base_time_normalized(self):
        s = _bare_scheduler()
        naive = datetime.now() - timedelta(days=2)
        nxt = s.calculate_next_run("* * * * *", base_time=naive)
        assert nxt.tzinfo is not None
        assert nxt > datetime.now(timezone.utc)


class TestEnqueueAtomicAdvance:
    """Task 2: atomic advance+enqueue, lost-CAS None, bounded jitter."""

    def _make_task(self, schedule_type="cron"):
        return SimpleNamespace(
            id=42,
            name="t",
            task_path="mod.fn",
            queue_name="default",
            priority=0,
            schedule_type=schedule_type,
            cron_expression="* * * * *",
            next_run_at=datetime.now(timezone.utc) - timedelta(seconds=30),
            max_retries=0,
            retry_backoff=1.0,
            allow_parallel=False,
            timeout_seconds=None,
            get_kwargs_dict=lambda: {},
        )

    def test_cron_calls_atomic_advance_and_returns_job(self):
        s = _bare_scheduler()
        sentinel_job = SimpleNamespace(id=7)
        s.backend.advance_scheduled_task_if_due.return_value = sentinel_job
        task = self._make_task()
        with patch("sqlery.core.scheduler.get_config", return_value=0):
            job = s._enqueue_for_scheduled_task(task)
        assert job is sentinel_job
        s.backend.advance_scheduled_task_if_due.assert_called_once()
        args, kwargs = s.backend.advance_scheduled_task_if_due.call_args
        call = list(args) + list(kwargs.values())
        # observed_due must be the task's pre-advance next_run_at
        assert task.next_run_at in call

    def test_cron_lost_cas_returns_none_no_fallback_create(self):
        s = _bare_scheduler()
        s.backend.advance_scheduled_task_if_due.return_value = None
        task = self._make_task()
        with patch("sqlery.core.scheduler.get_config", return_value=0):
            job = s._enqueue_for_scheduled_task(task)
        assert job is None
        s.backend.create_job.assert_not_called()

    def test_jitter_sleep_applied_when_positive(self):
        s = _bare_scheduler()
        s.backend.advance_scheduled_task_if_due.return_value = SimpleNamespace(id=1)
        task = self._make_task()
        with patch("sqlery.core.scheduler.get_config", return_value=2.0), patch(
            "sqlery.core.scheduler.time.sleep"
        ) as mock_sleep:
            s._enqueue_for_scheduled_task(task)
        mock_sleep.assert_called_once()
        slept = mock_sleep.call_args[0][0]
        assert 0 <= slept <= 2.0

    def test_no_jitter_sleep_when_zero(self):
        s = _bare_scheduler()
        s.backend.advance_scheduled_task_if_due.return_value = SimpleNamespace(id=1)
        task = self._make_task()
        with patch("sqlery.core.scheduler.get_config", return_value=0), patch(
            "sqlery.core.scheduler.time.sleep"
        ) as mock_sleep:
            s._enqueue_for_scheduled_task(task)
        mock_sleep.assert_not_called()

    def test_interval_branch_preserved(self):
        s = _bare_scheduler()
        task = self._make_task(schedule_type="interval")
        task.get_interval_seconds = lambda: 60
        s.backend.create_job.return_value = SimpleNamespace(id=9)
        with patch("sqlery.core.scheduler.get_config", return_value=0):
            job = s._enqueue_for_scheduled_task(task)
        assert job is not None
        s.backend.create_job.assert_called_once()
        s.backend.update_scheduled_task_next_run.assert_called_once()

    def test_once_branch_preserved(self):
        s = _bare_scheduler()
        task = self._make_task(schedule_type="once")
        s.backend.create_job.return_value = SimpleNamespace(id=10)
        with patch("sqlery.core.scheduler.get_config", return_value=0):
            job = s._enqueue_for_scheduled_task(task)
        assert job is not None
        s.backend.update_scheduled_task.assert_called_once_with(
            task.id, enabled=False, next_run_at=None
        )
