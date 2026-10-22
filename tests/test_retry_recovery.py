"""A crash between failing a job and creating its retry must not lose the retry."""

from unittest.mock import patch

import pytest

from sqlery.core.job import job
from sqlery.core.retry import is_retry_pending
from sqlery.core.worker import JobExecutor


@job(max_retries=3)
def always_fails():
    raise RuntimeError("boom")


@job(max_retries=3, autoretry_for=(ValueError,))
def fails_with_key_error():
    raise KeyError("not retryable")


@pytest.fixture
def backend(tmp_path):
    from sqlery.fastapi_sqlery import database
    from sqlery.fastapi_sqlery.backend import SQLAlchemyBackend

    database.init_database(f"sqlite:///{tmp_path / 'rr.db'}")
    return SQLAlchemyBackend()


def _claimed(backend, func, max_retries=3, retry_count=0):
    j = backend.create_job(task_path=func.task_path, kwargs={}, queue_name="default", priority=0,
                           scheduled_at=None, max_retries=max_retries, retry_backoff=1.0,
                           allow_parallel=False, timeout_seconds=None, retry_count=retry_count)
    return backend.claim_job_by_id(j.id, "w")


def _crash_before_retry(backend, claimed):
    """Run the job, killing the worker between failing it and creating the retry."""
    with patch.object(JobExecutor, "_retry_job", side_effect=SystemExit("worker killed")):
        with pytest.raises(SystemExit):
            JobExecutor(backend=backend).execute_job(claimed)


def test_failure_that_will_retry_is_marked_so_a_crash_is_recoverable(backend):
    j = _claimed(backend, always_fails)
    _crash_before_retry(backend, j)
    row = backend.get_job_by_id(j.id)
    assert row.status == "failed" and is_retry_pending(row.termination_reason)
    assert backend.get_latest_retry(j.id) is None            # the lost retry


def test_recovery_creates_the_lost_retry_exactly_once(backend):
    j = _claimed(backend, always_fails)
    _crash_before_retry(backend, j)
    ex = JobExecutor(backend=backend)
    assert ex.recover_lost_retries(grace_seconds=0) == 1
    retry = backend.get_latest_retry(j.id)
    assert retry is not None and retry.retry_count == 1 and retry.parent_job_id == j.id
    done = backend.get_job_by_id(j.id)
    assert done.status == "archived" and not is_retry_pending(done.termination_reason)
    assert ex.recover_lost_retries(grace_seconds=0) == 0     # idempotent: nothing left
    assert backend.get_latest_retry(j.id).id == retry.id


def test_recovery_respects_the_grace_period(backend):
    j = _claimed(backend, always_fails)
    _crash_before_retry(backend, j)
    # Just failed: its own worker may still be about to create the retry.
    assert JobExecutor(backend=backend).recover_lost_retries(grace_seconds=3600) == 0
    assert backend.get_latest_retry(j.id) is None


def test_retry_job_is_idempotent_against_a_racing_sweeper(backend):
    j = _claimed(backend, always_fails)
    _crash_before_retry(backend, j)
    ex = JobExecutor(backend=backend)
    failed = backend.get_job_by_id(j.id)
    first = ex._retry_job(failed)
    second = ex._retry_job(failed)
    assert first.id == second.id


def test_normal_retry_flow_is_unchanged_and_leaves_no_marker(backend):
    j = _claimed(backend, always_fails)
    JobExecutor(backend=backend).execute_job(j)
    row = backend.get_job_by_id(j.id)
    assert row.status == "archived" and not is_retry_pending(row.termination_reason)
    assert backend.get_latest_retry(j.id).retry_count == 1


def test_failures_that_must_not_retry_are_never_recovered(backend):
    # exception filtered out by autoretry_for
    filtered = _claimed(backend, fails_with_key_error)
    JobExecutor(backend=backend).execute_job(filtered)
    # retries exhausted
    spent = _claimed(backend, always_fails, max_retries=2, retry_count=2)
    JobExecutor(backend=backend).execute_job(spent)
    for j in (filtered, spent):
        row = backend.get_job_by_id(j.id)
        assert row.status == "failed" and not is_retry_pending(row.termination_reason)
    assert JobExecutor(backend=backend).recover_lost_retries(grace_seconds=0) == 0
    assert backend.get_latest_retry(filtered.id) is None and backend.get_latest_retry(spent.id) is None


def test_worker_loop_sweep_is_throttled(backend):
    from sqlery.core.worker import WorkerProcess

    w = WorkerProcess(queues=["default"], backend=backend)
    with patch.object(w.executor, "recover_lost_retries") as sweep:
        w._maybe_recover_lost_retries()
        w._maybe_recover_lost_retries()
    assert sweep.call_count == 1


def test_custom_backend_without_the_new_parameter_still_fails_jobs(backend):
    """Older/custom DatabaseBackend subclasses: no marker, but nothing breaks."""
    real = backend.mark_job_failed

    def legacy(job_id, error, traceback="", expected_version=None):   # no termination_reason
        return real(job_id, error, traceback, expected_version)

    j = _claimed(backend, always_fails)
    with patch.object(backend, "mark_job_failed", legacy):
        JobExecutor(backend=backend).execute_job(j)
    assert backend.get_latest_retry(j.id) is not None


# ---- Django ----------------------------------------------------------------------------
@pytest.mark.django_db
def test_django_executor_crash_window_is_recoverable():
    from sqlery.executor import TaskExecutor
    from sqlery.models import QueuedJob

    j = QueuedJob.objects.create(task_path="tests.test_retry_recovery.always_fails",
                                 queue_name="default", status="queued", max_retries=3)
    ex = TaskExecutor()
    with patch.object(TaskExecutor, "_retry_job", side_effect=SystemExit("worker killed")):
        with pytest.raises(SystemExit):
            ex.execute_job(j)
    j.refresh_from_db()
    assert j.status == "failed" and is_retry_pending(j.termination_reason)
    assert "exception" in j.termination_reason          # original reason preserved in the marker
    assert not QueuedJob.objects.filter(parent_job_id=j.id).exists()

    assert ex.recover_lost_retries(grace_seconds=0) == 1
    assert ex.recover_lost_retries(grace_seconds=0) == 0
    assert QueuedJob.objects.filter(parent_job_id=j.id).count() == 1
    j.refresh_from_db()
    assert j.termination_reason == "exception"          # marker cleared, reason restored


@pytest.mark.django_db
def test_django_normal_retry_and_non_retryable_failures_leave_no_marker():
    from sqlery.executor import TaskExecutor
    from sqlery.models import QueuedJob

    retried = QueuedJob.objects.create(task_path="tests.test_retry_recovery.always_fails",
                                       queue_name="default", status="queued", max_retries=3)
    filtered = QueuedJob.objects.create(task_path="tests.test_retry_recovery.fails_with_key_error",
                                        queue_name="default", status="queued", max_retries=3)
    ex = TaskExecutor()
    ex.execute_job(retried)
    ex.execute_job(filtered)
    for j in (retried, filtered):
        j.refresh_from_db()
        assert not is_retry_pending(j.termination_reason)
    assert QueuedJob.objects.filter(parent_job_id=retried.id).count() == 1
    assert not QueuedJob.objects.filter(parent_job_id=filtered.id).exists()
    assert ex.recover_lost_retries(grace_seconds=0) == 0
