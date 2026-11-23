"""#37: a failure while releasing a worker or stopping a job is logged, not swallowed, and
still does not raise (the worker loop must go on)."""

import logging

import pytest

pytest.importorskip("django")


@pytest.mark.django_db
def test_force_stop_logs_a_failing_mark_failed_and_still_clears_the_worker(caplog, monkeypatch):
    from sqlery.django_sqlery.models import QueuedJob

    job = QueuedJob.objects.create(task_path="operator.add", queue_name="default")
    job.status = "running"  # force_stop only looks at the in-memory status
    monkeypatch.setattr(job, "mark_failed", lambda **kw: 1 / 0)
    with caplog.at_level(logging.ERROR):
        assert job.force_stop() is True
    assert f"could not mark job {job.id} failed" in caplog.text
    assert "ZeroDivisionError" in caplog.text


@pytest.mark.django_db
def test_release_worker_logs_a_failure_and_does_not_raise(caplog, monkeypatch):
    from sqlery.django_sqlery import models

    job = models.QueuedJob.objects.create(task_path="operator.add", queue_name="default")
    job.worker_id = "00000000-0000-0000-0000-000000000001"
    monkeypatch.setattr(models.apps, "get_model", lambda *a: 1 / 0)
    with caplog.at_level(logging.ERROR):
        job._release_worker()
    assert f"Could not release worker {job.worker_id}" in caplog.text
