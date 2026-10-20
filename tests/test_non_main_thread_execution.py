"""Regression tests for #61: jobs must run outside the main thread (HTTP trigger)."""

import threading
from types import SimpleNamespace

import pytest

from sqlery.compat import get_backend
from sqlery.core.claiming import check_job_dependencies, expire_ttl_jobs
from sqlery.core.worker import JobExecutor
from sqlery.django_sqlery.models import ConcurrentModificationError, QueuedJob


def add_one(n):
    return n + 1


@pytest.mark.django_db(transaction=True)
def test_trigger_executes_a_job_from_a_non_main_thread():
    backend = get_backend()
    job = backend.create_job(
        task_path=f"{__name__}.add_one",
        kwargs={"n": 1},
        queue_name="q",
        priority=0,
        scheduled_at=None,
        max_retries=0,
        retry_backoff=1.0,
        allow_parallel=True,
        timeout_seconds=None,
        job_name="repro-thread",
    )
    claimed = backend.claim_job_by_id(job.id, "trigger-1")

    errors = []

    def run():
        try:
            JobExecutor(backend=backend).execute_job(claimed)
        except Exception as e:  # pragma: no cover - surfaced via assertion below
            errors.append(e)

    t = threading.Thread(target=run)
    t.start()
    t.join()
    assert not errors
    row = QueuedJob.objects.get(id=job.id)
    assert row.status == "success", (row.status, row.error)


class _RacyJob:
    id = 1
    ttl = 5
    dependencies = [1]

    def check_dependencies_met(self):
        return False, [2]

    def mark_failed(self, **kwargs):
        raise ConcurrentModificationError("lost the race")


def test_expire_ttl_jobs_ignores_lost_race():
    backend = SimpleNamespace(get_expired_ttl_jobs=lambda: [_RacyJob()])
    assert expire_ttl_jobs(backend) == 0


def test_check_job_dependencies_ignores_lost_race():
    assert check_job_dependencies(_RacyJob()) is False


def test_other_mark_failed_errors_still_propagate():
    class Boom(_RacyJob):
        def mark_failed(self, **kwargs):
            raise RuntimeError("db down")

    with pytest.raises(RuntimeError):
        check_job_dependencies(Boom())
