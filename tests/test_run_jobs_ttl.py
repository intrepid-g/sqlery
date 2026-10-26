"""Regression #62: run_jobs (TaskExecutor.run_queue_workers) must enforce a queued job's ttl."""

from datetime import timedelta

import pytest
from django.utils import timezone


@pytest.mark.django_db(transaction=True)
def test_queued_job_past_its_ttl_is_expired_not_run():
    from sqlery.compat import get_backend
    from sqlery.core.worker import TaskExecutor
    from sqlery.django_sqlery.models import JobAll, QueuedJob

    job = get_backend().create_job(
        task_path="builtins.print",
        kwargs={},
        queue_name="default",
        priority=0,
        scheduled_at=None,
        max_retries=0,
        retry_backoff=1.0,
        allow_parallel=True,
        timeout_seconds=None,
        job_name="ttl-repro",
        ttl=1,
    )
    QueuedJob.objects.filter(id=job.id).update(created_at=timezone.now() - timedelta(seconds=30))

    TaskExecutor().run_queue_workers(once=True)

    job = JobAll.objects.get(id=job.id)  # an expired job is final: in history
    assert (job.status, job.termination_reason) == ("failed", "expired")
