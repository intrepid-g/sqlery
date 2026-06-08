"""Tests for atomic scheduled task claiming.

FAILING TESTS EXPLANATION:
These tests are failing (with ERRORS) because SQLite (used in tests) doesn't support
concurrent access patterns that these tests rely on.

Specific issues:
1. SQLite doesn't support SELECT FOR UPDATE SKIP LOCKED - it's PostgreSQL/MySQL only.
2. Threading tests cause "database table is locked" errors with SQLite's file locking.
3. Concurrent transaction tests don't work properly with SQLite's locking model.

Error seen: "django.db.utils.OperationalError: database table is locked: sqlery_scheduled_task"

The tests would pass with PostgreSQL which supports proper row-level locking.

To fix: Either:
- Skip these tests when using SQLite: @pytest.mark.skipif(connection.vendor == 'sqlite', ...)
- Run tests against PostgreSQL for full concurrency testing
- Refactor tests to not rely on true concurrent database access
"""

import pytest
import threading
import time
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest import mock

from django.db import connection, transaction
from django.utils import timezone
from sqlery.models import ScheduledTask, QueuedJob
from sqlery.executor import TaskExecutor
from sqlery.compat import get_backend
from sqlery.core.scheduler import Scheduler

skip_on_sqlite = pytest.mark.skipif(
    connection.vendor == "sqlite",
    reason="SQLite does not support SELECT FOR UPDATE SKIP LOCKED or concurrent transactions"
)


# Test task
def test_task():
    """Simple test task."""
    return "completed"


@pytest.mark.django_db(transaction=True)
class TestAtomicSchedulerClaiming:
    """Test atomic scheduled task claiming to prevent duplicate enqueueing."""

    def test_select_for_update_used_in_run_due_tasks(self):
        """Test that run_due_tasks uses select_for_update for task claiming."""
        # Create a due scheduled task
        task = ScheduledTask.objects.create(
            name="Test Task",
            task_path="tests.test_atomic_scheduler.test_task",
            cron_expression="* * * * *",
            queue_name="default",
            priority=5,
            enabled=True,
            next_run_at=timezone.now() - timedelta(seconds=10),
        )

        executor = TaskExecutor()

        # Run scheduler
        jobs = executor.run_due_tasks()

        # Should have created one job
        assert len(jobs) == 1
        assert jobs[0].scheduled_task == task
        assert jobs[0].status == "queued"

        # Task's next_run_at should be updated
        task.refresh_from_db()
        assert task.next_run_at > timezone.now()

    @skip_on_sqlite
    def test_concurrent_schedulers_no_duplicate_enqueueing(self):
        """Test that concurrent schedulers don't enqueue duplicate jobs."""
        # Create multiple due scheduled tasks
        tasks = []
        for i in range(5):
            task = ScheduledTask.objects.create(
                name=f"Test Task {i}",
                task_path="tests.test_atomic_scheduler.test_task",
                cron_expression="* * * * *",
                queue_name="default",
                priority=5,
                enabled=True,
                next_run_at=timezone.now() - timedelta(seconds=10),
            )
            tasks.append(task)

        enqueued_jobs = []
        job_lock = threading.Lock()

        def run_scheduler():
            """Run scheduler in a thread."""
            executor = TaskExecutor()
            jobs = executor.run_due_tasks()
            with job_lock:
                enqueued_jobs.extend(jobs)

        # Start multiple schedulers concurrently
        threads = []
        for i in range(3):
            thread = threading.Thread(target=run_scheduler)
            thread.start()
            threads.append(thread)

        # Wait for all schedulers to finish
        for thread in threads:
            thread.join()

        # Verify each task was enqueued exactly once
        assert len(enqueued_jobs) == 5

        # Check all jobs are unique (no duplicates)
        job_ids = [job.id for job in enqueued_jobs]
        assert len(set(job_ids)) == 5

        # Verify each task has exactly one queued job
        for task in tasks:
            jobs_for_task = QueuedJob.objects.filter(scheduled_task=task)
            assert jobs_for_task.count() == 1

    @skip_on_sqlite
    def test_skip_locked_prevents_scheduler_blocking(self):
        """Test that SKIP LOCKED prevents schedulers from blocking on same task."""
        task = ScheduledTask.objects.create(
            name="Test Task",
            task_path="tests.test_atomic_scheduler.test_task",
            cron_expression="* * * * *",
            queue_name="default",
            priority=5,
            enabled=True,
            next_run_at=timezone.now() - timedelta(seconds=10),
        )

        claimed_by = []
        claim_lock = threading.Lock()

        def claim_task(scheduler_id):
            """Try to claim and process task."""
            with transaction.atomic():
                try:
                    locked_task = ScheduledTask.objects.select_for_update(
                        skip_locked=True
                    ).get(id=task.id, enabled=True)

                    with claim_lock:
                        claimed_by.append(scheduler_id)

                    time.sleep(0.2)  # Hold the lock
                except ScheduledTask.DoesNotExist:
                    # Task was already claimed
                    pass

        # Scheduler 1 claims task
        thread1 = threading.Thread(target=claim_task, args=("scheduler1",))
        thread1.start()

        time.sleep(0.05)  # Ensure thread1 acquires lock first

        # Scheduler 2 should skip locked task immediately
        thread2 = threading.Thread(target=claim_task, args=("scheduler2",))
        thread2.start()

        thread1.join()
        thread2.join()

        # Only one scheduler should have claimed the task
        assert len(claimed_by) == 1

    def test_scheduler_updates_next_run_at_atomically(self):
        """Test that next_run_at is updated within the claiming transaction."""
        task = ScheduledTask.objects.create(
            name="Test Task",
            task_path="tests.test_atomic_scheduler.test_task",
            cron_expression="@hourly",
            queue_name="default",
            priority=5,
            enabled=True,
            next_run_at=timezone.now() - timedelta(seconds=10),
        )

        old_next_run = task.next_run_at
        executor = TaskExecutor()

        jobs = executor.run_due_tasks()

        # Should have created job
        assert len(jobs) == 1

        # next_run_at should be updated
        task.refresh_from_db()
        assert task.next_run_at > old_next_run
        assert task.next_run_at > timezone.now()

    def test_disabled_task_not_enqueued(self):
        """Test that disabled tasks are not enqueued even if due."""
        task = ScheduledTask.objects.create(
            name="Test Task",
            task_path="tests.test_atomic_scheduler.test_task",
            cron_expression="* * * * *",
            queue_name="default",
            priority=5,
            enabled=False,  # Disabled
            next_run_at=timezone.now() - timedelta(seconds=10),
        )

        executor = TaskExecutor()
        jobs = executor.run_due_tasks()

        # No jobs should be created
        assert len(jobs) == 0
        assert QueuedJob.objects.filter(scheduled_task=task).count() == 0

    def test_already_queued_task_not_duplicated(self):
        """Test that task with existing queued job is not enqueued again."""
        task = ScheduledTask.objects.create(
            name="Test Task",
            task_path="tests.test_atomic_scheduler.test_task",
            cron_expression="* * * * *",
            queue_name="default",
            priority=5,
            enabled=True,
            next_run_at=timezone.now() - timedelta(seconds=10),
        )

        # Manually create a queued job for this task
        existing_job = QueuedJob.objects.create(
            task_path=task.task_path,
            queue_name=task.queue_name,
            priority=task.priority,
            scheduled_task=task,
            status="queued",
        )

        executor = TaskExecutor()
        jobs = executor.run_due_tasks()

        # No new jobs should be created
        assert len(jobs) == 0
        assert QueuedJob.objects.filter(scheduled_task=task).count() == 1

    def test_running_task_not_duplicated(self):
        """Test that task with running job is not enqueued again."""
        task = ScheduledTask.objects.create(
            name="Test Task",
            task_path="tests.test_atomic_scheduler.test_task",
            cron_expression="* * * * *",
            queue_name="default",
            priority=5,
            enabled=True,
            next_run_at=timezone.now() - timedelta(seconds=10),
        )

        # Manually create a running job for this task
        running_job = QueuedJob.objects.create(
            task_path=task.task_path,
            queue_name=task.queue_name,
            priority=task.priority,
            scheduled_task=task,
            status="queued",
        )
        running_job.mark_running()

        executor = TaskExecutor()
        jobs = executor.run_due_tasks()

        # No new jobs should be created
        assert len(jobs) == 0
        assert QueuedJob.objects.filter(scheduled_task=task, status="queued").count() == 0


@skip_on_sqlite
@pytest.mark.django_db(transaction=True)
class TestAtomicSchedulerPerformance:
    """Test performance characteristics of atomic scheduler claiming."""

    def test_skip_locked_doesnt_wait(self):
        """Test that SKIP LOCKED returns immediately without waiting."""
        task = ScheduledTask.objects.create(
            name="Test Task",
            task_path="tests.test_atomic_scheduler.test_task",
            cron_expression="* * * * *",
            queue_name="default",
            priority=5,
            enabled=True,
            next_run_at=timezone.now() - timedelta(seconds=10),
        )

        def hold_lock():
            """Hold lock on task for 500ms."""
            with transaction.atomic():
                locked_task = ScheduledTask.objects.select_for_update(
                    skip_locked=True
                ).get(id=task.id)
                time.sleep(0.5)

        thread1 = threading.Thread(target=hold_lock)
        thread1.start()

        time.sleep(0.1)  # Ensure thread1 has lock

        # Scheduler 2 tries to claim - should return immediately
        start = time.time()
        executor = TaskExecutor()
        jobs = executor.run_due_tasks()
        elapsed = time.time() - start

        # Should be fast (< 200ms), not wait for lock (500ms)
        assert elapsed < 0.2
        # Should return empty since task is locked
        assert len(jobs) == 0

        thread1.join()

    def test_multiple_tasks_claimed_by_different_schedulers(self):
        """Test that multiple schedulers can claim different tasks simultaneously."""
        # Create 10 due tasks
        tasks = []
        for i in range(10):
            task = ScheduledTask.objects.create(
                name=f"Test Task {i}",
                task_path="tests.test_atomic_scheduler.test_task",
                cron_expression="* * * * *",
                queue_name="default",
                priority=5,
                enabled=True,
                next_run_at=timezone.now() - timedelta(seconds=10),
            )
            tasks.append(task)

        enqueued_jobs = []
        job_lock = threading.Lock()

        def run_scheduler():
            """Run scheduler in a thread."""
            executor = TaskExecutor()
            jobs = executor.run_due_tasks()
            with job_lock:
                enqueued_jobs.extend(jobs)

        # Start 5 concurrent schedulers
        threads = []
        for i in range(5):
            thread = threading.Thread(target=run_scheduler)
            thread.start()
            threads.append(thread)

        # Wait for all to finish
        for thread in threads:
            thread.join()

        # All 10 tasks should have been enqueued exactly once
        assert len(enqueued_jobs) == 10
        job_ids = [job.id for job in enqueued_jobs]
        assert len(set(job_ids)) == 10

        # Verify each task has exactly one job
        for task in tasks:
            assert QueuedJob.objects.filter(scheduled_task=task).count() == 1


@pytest.mark.django_db(transaction=True)
class TestCronSemanticsHardening:
    """DB-backed proof of the four CRON behaviors (Phase 10).

    These tests exercise the reworked atomic firing path (Plans 01/03) against the
    real DB. The single-fire test deliberately runs on SQLite (NOT @skip_on_sqlite)
    because the CAS on the observed next_run_at makes exactly-once firing
    engine-independent, unlike the old SELECT FOR UPDATE SKIP LOCKED path. The full
    {Django, standalone} x {SQLite, Postgres} parity matrix is deferred to Phase 11.

    IMPORTANT — which scheduler is under test:
    The plan referenced ``TaskExecutor`` (sqlery.executor) as "the Scheduler alias",
    but that name resolves to the LEGACY Django ``_executor_impl.TaskExecutor``,
    which was NOT reworked in Plans 01/03 (it still uses SELECT FOR UPDATE SKIP
    LOCKED, computes next_run_at from wall-clock now, never calls
    advance_scheduled_task_if_due, and applies no jitter). The hardened path that
    the daemon and the Phase 9 worker-elected scheduler actually run at runtime is
    ``sqlery.core.scheduler.Scheduler`` wired to ``get_backend()`` (DjangoBackend
    here). These tests therefore drive that core Scheduler so they prove the real
    CRON-01..04 guarantees. (See SUMMARY "Deviations" — Rule 1.)
    """

    @staticmethod
    def _scheduler():
        """Build the hardened core Scheduler wired to the active (Django) backend."""
        return Scheduler(backend=get_backend())

    @staticmethod
    def _make_due_cron_task(name="cron-hardening", cron="*/5 * * * *", past_seconds=10):
        """Create an enabled cron task whose next_run_at is a fixed time in the past.

        The past next_run_at is written via a queryset .update() so the model's
        save()-time next_run_at recalculation does not clobber the value we set.
        """
        due = datetime.now(dt_timezone.utc) - timedelta(seconds=past_seconds)
        task = ScheduledTask.objects.create(
            name=name,
            task_path="tests.test_atomic_scheduler.test_task",
            cron_expression=cron,
            queue_name="default",
            priority=5,
            enabled=True,
            schedule_type="cron",
            next_run_at=due,
        )
        # Bypass save() recalculation to pin an exact, drift-free scheduled time.
        ScheduledTask.objects.filter(id=task.id).update(next_run_at=due)
        task.refresh_from_db()
        return task

    def test_cron_fires_exactly_once_under_simulated_overlap(self):
        """Two leaders observing the same due tick produce exactly one QueuedJob.

        Runs on SQLite: the CAS on observed next_run_at is engine-independent.
        We read observed_due once, then make two advance attempts against the
        backend with that SAME observed_due (the way two overlapping leaders would
        each have read the row before either advanced). Exactly one wins.
        """
        task = self._make_due_cron_task()
        scheduler = self._scheduler()
        backend = scheduler.backend

        observed_due = task.next_run_at
        new_next_run = scheduler.calculate_next_run(task.cron_expression, base_time=observed_due)
        job_kwargs = {
            "task_path": task.task_path,
            "kwargs": {},
            "queue_name": task.queue_name,
            "priority": task.priority,
            "scheduled_at": None,
            "max_retries": 0,
            "retry_backoff": 1.0,
            "allow_parallel": False,
            "timeout_seconds": None,
            "scheduled_task_id": task.id,
        }

        # Both attempts use the SAME observed_due — the second is now stale once
        # the first has advanced the row. Exactly one CAS wins.
        job_a = backend.advance_scheduled_task_if_due(
            task.id, observed_due, new_next_run, job_kwargs
        )
        job_b = backend.advance_scheduled_task_if_due(
            task.id, observed_due, new_next_run, job_kwargs
        )

        winners = [j for j in (job_a, job_b) if j is not None]
        assert len(winners) == 1, "exactly one advance attempt must win the CAS"
        assert QueuedJob.objects.filter(scheduled_task_id=task.id).count() == 1

        # The row advanced exactly once, to the computed next occurrence.
        task.refresh_from_db()
        assert task.next_run_at == new_next_run

    def test_cron_fires_exactly_once_under_threaded_overlap(self):
        """Threaded two-leader firing of the same due task still yields one job.

        Complements the sequential test by exercising real thread overlap through
        the full run_due_tasks path. Engine-independent (CAS), so not skipped on
        SQLite. The assertion is on the durable invariant: exactly one QueuedJob.
        """
        task = self._make_due_cron_task(name="cron-threaded")

        results = []
        results_lock = threading.Lock()

        def fire():
            scheduler = self._scheduler()
            jobs = scheduler.run_due_tasks()
            mine = [j for j in jobs if j.scheduled_task_id == task.id]
            with results_lock:
                results.extend(mine)

        threads = [threading.Thread(target=fire) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert QueuedJob.objects.filter(scheduled_task_id=task.id).count() == 1
        assert len(results) == 1

    def test_next_run_at_advances_without_drift_across_ticks(self):
        """next_run_at advances from the SCHEDULED time, not wall-clock now.

        Each fired tick must set next_run_at to calculate_next_run computed from the
        PRIOR scheduled next_run_at (future-clamped), so a slow scheduler does not
        accumulate drift. Asserts monotonic, drift-free advance across several ticks.
        """
        task = self._make_due_cron_task(name="cron-drift", cron="*/5 * * * *")
        scheduler = self._scheduler()

        prior_scheduled = task.next_run_at
        last_next = prior_scheduled
        for _ in range(3):
            expected = scheduler.calculate_next_run(task.cron_expression, base_time=last_next)
            jobs = scheduler.run_due_tasks()
            fired = [j for j in jobs if j.scheduled_task_id == task.id]
            task.refresh_from_db()

            if fired:
                # When the task fired, the advance is computed from the scheduled
                # time, not from now, and is strictly after the prior value.
                assert task.next_run_at == expected
                assert task.next_run_at > last_next
                last_next = task.next_run_at
                # Re-arm the task as due for the next tick by rewinding it to a
                # past scheduled time derived from the LAST scheduled occurrence
                # (drift-free) so the next iteration fires again.
                rewound = last_next - timedelta(minutes=5)
                ScheduledTask.objects.filter(id=task.id).update(next_run_at=rewound)
                task.refresh_from_db()
                last_next = rewound
            # If not fired (already future), the loop simply re-checks; the pinned
            # past next_run_at guarantees at least the first iteration fires.

        # Final next_run_at is in the future (clamped) once we let it settle.
        scheduler.run_due_tasks()
        task.refresh_from_db()
        assert task.next_run_at > datetime.now(dt_timezone.utc)

    def test_far_behind_task_clamps_to_future_occurrence(self):
        """A task many ticks behind clamps to a single future occurrence (no replay)."""
        task = self._make_due_cron_task(name="cron-far-behind", cron="*/5 * * * *", past_seconds=0)
        # Pin next_run_at to one year ago — far behind.
        far_past = datetime.now(dt_timezone.utc) - timedelta(days=365)
        ScheduledTask.objects.filter(id=task.id).update(next_run_at=far_past)
        task.refresh_from_db()

        scheduler = self._scheduler()
        jobs = scheduler.run_due_tasks()
        fired = [j for j in jobs if j.scheduled_task_id == task.id]
        assert len(fired) == 1, "far-behind task fires exactly once"

        # Exactly one job (no missed-tick replay) and next_run_at is in the future.
        assert QueuedJob.objects.filter(scheduled_task_id=task.id).count() == 1
        task.refresh_from_db()
        assert task.next_run_at > datetime.now(dt_timezone.utc)

    def test_scheduler_jitter_seconds_respected(self):
        """Jitter knob >0 applies a bounded delay; 0 applies no sleep.

        Timing-tolerant: we patch sqlery.core.scheduler.time.sleep and assert the
        delay argument is within [0, jitter] when configured >0, and that sleep is
        NOT called when jitter is 0. We drive the jitter value through the
        scheduler's _get_jitter_seconds resolution by patching it, since the Django
        get_config path reads DJANGO_SQL_JOBS only (per 10-02-SUMMARY).
        """
        jitter_value = 0.5

        # Jitter > 0: a bounded sleep in [0, jitter] is applied before the advance.
        self._make_due_cron_task(name="cron-jitter-on")
        scheduler = self._scheduler()
        with (
            mock.patch.object(scheduler, "_get_jitter_seconds", return_value=jitter_value),
            mock.patch("sqlery.core.scheduler.time.sleep") as mock_sleep,
        ):
            scheduler.run_due_tasks()
            assert mock_sleep.called, "jitter > 0 must apply a sleep on the cron path"
            delay_arg = mock_sleep.call_args.args[0]
            assert 0 <= delay_arg <= jitter_value

        # Jitter == 0: no sleep is applied.
        self._make_due_cron_task(name="cron-jitter-off")
        scheduler0 = self._scheduler()
        with (
            mock.patch.object(scheduler0, "_get_jitter_seconds", return_value=0),
            mock.patch("sqlery.core.scheduler.time.sleep") as mock_sleep0,
        ):
            scheduler0.run_due_tasks()
            assert not mock_sleep0.called, "jitter == 0 must not sleep"

    def test_interval_and_once_not_regressed(self):
        """Interval re-advances by its interval; once disables itself after firing."""
        scheduler = self._scheduler()

        # Interval task: fires, then next_run_at advances to ~now + interval.
        interval_task = ScheduledTask.objects.create(
            name="interval-task",
            task_path="tests.test_atomic_scheduler.test_task",
            queue_name="default",
            priority=5,
            enabled=True,
            schedule_type="interval",
            interval=5,
            interval_unit="minutes",
            next_run_at=datetime.now(dt_timezone.utc) - timedelta(seconds=10),
        )
        ScheduledTask.objects.filter(id=interval_task.id).update(
            next_run_at=datetime.now(dt_timezone.utc) - timedelta(seconds=10)
        )

        before = datetime.now(dt_timezone.utc)
        jobs = scheduler.run_due_tasks()
        fired = [j for j in jobs if j.scheduled_task_id == interval_task.id]
        assert len(fired) == 1
        interval_task.refresh_from_db()
        # Advanced by ~the interval (5 minutes = 300s) from now.
        delta = interval_task.next_run_at - before
        assert timedelta(seconds=290) <= delta <= timedelta(seconds=310)

        # Once task: fires, then is disabled with next_run_at cleared.
        once_task = ScheduledTask.objects.create(
            name="once-task",
            task_path="tests.test_atomic_scheduler.test_task",
            queue_name="default",
            priority=5,
            enabled=True,
            schedule_type="once",
            scheduled_time=datetime.now(dt_timezone.utc) - timedelta(seconds=10),
            next_run_at=datetime.now(dt_timezone.utc) - timedelta(seconds=10),
        )
        ScheduledTask.objects.filter(id=once_task.id).update(
            next_run_at=datetime.now(dt_timezone.utc) - timedelta(seconds=10)
        )

        jobs = scheduler.run_due_tasks()
        fired_once = [j for j in jobs if j.scheduled_task_id == once_task.id]
        assert len(fired_once) == 1
        once_task.refresh_from_db()
        assert once_task.enabled is False
        assert once_task.next_run_at is None
