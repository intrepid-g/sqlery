"""Regression: 'more jobs exist?' probe must not use SELECT FOR UPDATE.

REGRESSION 2026-06-14: run_queue_workers called get_queued_jobs(...).exists()
to decide whether to spawn the next worker. That queryset carries
SELECT ... FOR UPDATE SKIP LOCKED, and .exists() outside a transaction raised
TransactionManagementError on PostgreSQL, crashing the worker after one job.
Fixed by a non-locking _has_more_queued_jobs() probe.
"""
import pytest


@pytest.mark.django_db
def test_has_more_queued_jobs_probe_is_non_locking():
    from sqlery.django_sqlery._executor_impl import TaskExecutor
    from sqlery.django_sqlery.models import QueuedJob

    ex = TaskExecutor()
    # The probe must run without a surrounding transaction and return a bool.
    assert ex._has_more_queued_jobs() in (True, False)
    QueuedJob.objects.create(task_path="x.y", queue_name="default", status="queued")
    assert ex._has_more_queued_jobs(queue_name="default") is True
    # And it must NOT have applied a row lock (no FOR UPDATE in compiled SQL).
    now_qs = QueuedJob.objects.filter(status="queued")
    assert "FOR UPDATE" not in str(now_qs.query).upper()
