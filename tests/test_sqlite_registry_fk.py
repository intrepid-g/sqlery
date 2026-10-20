"""Regression: deleting a QueuedJob must not trip FKs on SQLite (#81)."""

import pytest
from django.db import connection
from django.utils import timezone

from sqlery.django_sqlery.models import JobRegistry, QueuedJob, Worker

pytestmark = pytest.mark.django_db(transaction=True)

sqlite_only = pytest.mark.skipif(connection.vendor != "sqlite", reason="SQLite schema check")


def _job():
    return QueuedJob.objects.create(
        task_path="os.getpid",
        queue_name="default",
        status="success",
        finished_at=timezone.now(),
        result_ttl=1,
    )


def test_job_with_registry_rows_can_be_deleted():
    job = _job()
    JobRegistry.objects.create(job_id=job.id, registry_type="started")
    job.delete()
    assert not QueuedJob.objects.filter(id=job.id).exists()


def test_job_that_is_a_workers_current_job_can_be_deleted():
    job = _job()
    worker = Worker.objects.create(pid=1, current_job_id=job.id)
    job.delete()
    assert Worker.objects.filter(pk=worker.pk).exists()


@sqlite_only
def test_no_job_foreign_keys_remain():
    with connection.cursor() as cur:
        for table in ("sqlery_registry", "sqlery_worker"):
            cur.execute(f'PRAGMA foreign_key_list("{table}")')
            targets = [row[2] for row in cur.fetchall()]
            assert "sqlery_queued_job" not in targets, table
