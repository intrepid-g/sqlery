"""TaskExecutor retries keep a findable job_name (``<base>:retry:<n>``), like core/worker.py."""

import pytest

from sqlery.compat import get_backend
from sqlery.django_sqlery._executor_impl import TaskExecutor
from sqlery.django_sqlery.models import JobAll, QueuedJob


@pytest.mark.django_db(transaction=True)
def test_retry_keeps_a_findable_name():
    get_backend().create_job(
        task_path="builtins.int",
        kwargs={"x": "boom"},  # int(x="boom") raises TypeError -> job fails and is retried
        queue_name="q",
        priority=0,
        scheduled_at=None,
        max_retries=1,
        retry_backoff=0.0,
        allow_parallel=True,
        timeout_seconds=None,
        job_name="repro:flaky:0",
    )
    TaskExecutor().run_queue_workers(queue_name="q", once=True)
    # the failed first attempt is final: it is in history (read the union view)
    first = JobAll.objects.get(job_name="repro:flaky:0")
    retry = JobAll.objects.get(parent_job_id=first.id)
    assert retry.job_name == "repro:flaky:0:retry:1"


@pytest.mark.django_db
def test_retry_of_unnamed_job_stays_unnamed():
    failed = QueuedJob.objects.create(
        task_path="builtins.int", queue_name="q", max_retries=2, status="failed"
    )
    retry = TaskExecutor()._retry_job(failed)
    assert retry.job_name is None
