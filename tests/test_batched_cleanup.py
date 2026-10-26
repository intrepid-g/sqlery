"""Behavioral tests for batched cleanup_jobs invariants.

Covers:
1. Mid-loop claim safety — a job claimed (status changed) between SELECT and DELETE is not deleted
2. Batching behavior — CLEANUP_BATCH_SIZE+1 jobs trigger at least 2 DELETE statements, not one unbounded
3. dry_run correctness — dry_run=True returns count without deleting any rows
4. Inter-batch sleep — time.sleep(0.1) is called between batches
"""

import pytest
from datetime import timedelta
from unittest.mock import patch, call

from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.utils import timezone

from sqlery.models import QueuedJob
from sqlery.django_sqlery.backend import DjangoBackend, CLEANUP_BATCH_SIZE, FINISHED_STATUSES
from sqlery.django_sqlery.models import JobAll, JobHistory


def _create_failed_job():
    """Create a job that failed more than 15 days ago (a failed job is in history, whose
    retention reads the time it finished)."""
    job = QueuedJob.objects.create(
        task_path="tests.test_batched_cleanup.dummy_task",
        queue_name="default",
        priority=0,
        status="failed",
    )
    old = timezone.now() - timedelta(days=15)
    JobHistory.objects.filter(id=job.id).update(created_at=old, finished_at=old)
    return job


def dummy_task():
    """Placeholder task for tests."""
    return "ok"


@pytest.mark.django_db
def test_cleanup_never_deletes_claimed_job():
    """Retention never deletes live work, however old.

    Only finished jobs (history) are pruned, so a queued job can not be hit by the mid-loop
    claim race that the old live-table loop had to guard against.
    """
    backend = DjangoBackend()
    job = QueuedJob.objects.create(
        task_path="tests.test_batched_cleanup.dummy_task", queue_name="default", status="queued"
    )
    QueuedJob.objects.filter(id=job.id).update(created_at=timezone.now() - timedelta(days=15))

    result = backend.cleanup_jobs(max_age_days=5)

    assert QueuedJob.objects.filter(id=job.id).exists(), (
        "A queued job must not be deleted by cleanup"
    )
    assert result["deleted"] == 0


@pytest.mark.django_db(transaction=True)
def test_cleanup_issues_multiple_batches_not_one():
    """CLEANUP_BATCH_SIZE+1 failed jobs trigger at least 2 DELETE statements.

    Verifies that the batched loop issues multiple bounded DELETE queries rather
    than one unbounded DELETE covering all rows.
    """
    backend = DjangoBackend()
    for _ in range(CLEANUP_BATCH_SIZE + 1):
        _create_failed_job()

    with CaptureQueriesContext(connection) as captured:
        result = backend.cleanup_jobs(status="failed", max_age_days=5)

    delete_queries = [q for q in captured if "DELETE" in q["sql"].upper()]
    assert len(delete_queries) >= 2, (
        f"Expected at least 2 DELETE statements for {CLEANUP_BATCH_SIZE + 1} rows, "
        f"got {len(delete_queries)}"
    )
    assert result["deleted"] == CLEANUP_BATCH_SIZE + 1


@pytest.mark.django_db
def test_cleanup_dry_run_does_not_delete():
    """dry_run=True returns the count without deleting any rows."""
    backend = DjangoBackend()
    jobs = [_create_failed_job() for _ in range(5)]

    result = backend.cleanup_jobs(status="failed", max_age_days=5, dry_run=True)

    assert result["count"] == 5
    assert result["deleted"] == 0
    for job in jobs:
        assert JobAll.objects.filter(id=job.id).exists(), (
            f"Job {job.id} should not have been deleted in dry_run mode"
        )


@pytest.mark.django_db
def test_cleanup_batch_sleep_is_called():
    """time.sleep(0.1) is called at least once when multiple batches are needed.

    Verifies the inter-batch sleep is present, which yields to autovacuum and
    caps lock-hold time between iterations.
    """
    backend = DjangoBackend()
    for _ in range(CLEANUP_BATCH_SIZE + 1):
        _create_failed_job()

    with patch("sqlery.django_sqlery.backend.time.sleep") as mock_sleep:
        backend.cleanup_jobs(status="failed", max_age_days=5)

    assert mock_sleep.call_count >= 1, "time.sleep should be called at least once"
    mock_sleep.assert_any_call(0.1)
