"""Tests for sqlery.compat.rq in standalone (non-Django) mode.

These tests exercise the standalone code paths by patching is_django_mode to False
and injecting a MockBackend. No Django test infrastructure (pytest-django, django_db
marker) is used — the mocks replace all DB operations.
"""

from __future__ import annotations

import pytest
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Any
from unittest.mock import patch, MagicMock

from sqlery.compat import DatabaseBackend


# ---------------------------------------------------------------------------
# Helpers: FakeJob, MockBackend
# ---------------------------------------------------------------------------


@dataclass
class FakeJob:
    """Minimal fake job object matching the field names on both Django and SA models."""

    id: int = 1
    status: str = "queued"
    queue_name: str = "default"
    scheduled_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC))
    meta: dict | None = None
    task_path: str = "myapp.tasks.my_task"
    kwargs: dict = field(default_factory=dict)
    max_retries: int = 3
    retry_count: int = 0
    retry_backoff: float = 1.0
    priority: int = 0
    job_name: str | None = None

    @property
    def pk(self):
        return self.id


class MockBackend(DatabaseBackend):
    """Stub backend that implements all abstract methods with configurable returns."""

    def __init__(self):
        self._jobs: list[FakeJob] = []
        self._count: int = 0
        self._workers: list[Any] = []
        self._job_by_id: FakeJob | None = None
        self.cancelled_ids: list[int] = []

    # --- Configurable methods ---

    def get_jobs(
        self,
        status: str | None = None,
        queue_name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list:
        result = self._jobs
        if status:
            result = [j for j in result if j.status == status]
        if queue_name:
            result = [j for j in result if j.queue_name == queue_name]
        return result[:limit]

    def count_jobs(self, status: str | None = None, queue_name: str | None = None) -> int:
        return self._count

    def cleanup_jobs(self, status=None, max_age_days=None, max_count=None, queue_name=None, dry_run=False) -> dict:
        return {"deleted": 2}

    def get_job_by_id(self, job_id: int):
        return self._job_by_id

    def cancel_job(self, job_id: int) -> bool:
        self.cancelled_ids.append(job_id)
        return True

    def get_worker_heartbeats(self, active_only: bool = True) -> list:
        return self._workers

    # --- Remaining abstract methods stubbed ---

    def create_job(self, task_path, kwargs, queue_name, priority, scheduled_at, max_retries,
                   retry_backoff, allow_parallel, timeout_seconds, **kw):
        return FakeJob()

    def claim_job(self, queues, worker_id):
        return None

    def get_queue_stats(self, queue_name=None) -> dict:
        return {}

    def retry_failed_jobs(self, queue_name=None, max_jobs=None) -> int:
        return 0

    def get_due_scheduled_tasks(self):
        return []

    def create_scheduled_task(self, name, task_path, cron_expression, queue_name, priority, enabled=True):
        return None

    def update_worker_heartbeat(self, worker_id, status, current_job_id=None):
        pass

    def cleanup_jobs_by_count(self, status=None, keep_count=1000, queue_name=None, dry_run=False) -> dict:
        return {"deleted": 0}

    def get_database_stats(self) -> dict:
        return {}

    def vacuum_database(self) -> dict:
        return {}

    def add_job_to_registry(self, job_id, registry_type, metadata=None):
        pass

    def remove_job_from_registry(self, job_id, registry_type):
        pass

    def get_registry_jobs(self, registry_type, queue_name=None, limit=None) -> list:
        return []

    def cleanup_registry(self, registry_type=None, max_age_days=None) -> dict:
        return {}

    def mark_job_success(self, job_id, output=""):
        return None

    def mark_job_failed(self, job_id, error, traceback=""):
        return None

    def mark_job_archived(self, job_id):
        pass

    def cascade_ancestor_status(self, job_id, status):
        pass

    def has_pending_job_for_scheduled_task(self, task_id) -> bool:
        return False

    def update_scheduled_task_next_run(self, task_id, next_run_at):
        pass

    def update_scheduled_task(self, task_id, **updates):
        return None

    def delete_scheduled_task(self, task_id) -> bool:
        return False

    def get_scheduled_tasks(self, enabled_only=False) -> list:
        return []

    def get_scheduled_task(self, task_id):
        return None

    def get_running_jobs(self, queue_name=None) -> list:
        return []

    def has_running_jobs_in_queue(self, queue_name, exclude_job_id=None) -> bool:
        return False

    def release_job(self, job_id):
        pass


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def standalone_backend():
    """Return a MockBackend and patch is_django_mode + get_backend in rq.py."""
    backend = MockBackend()
    with (
        patch("sqlery.compat.rq.is_django_mode", return_value=False),
        patch("sqlery.compat.rq.get_backend", return_value=backend),
    ):
        yield backend


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_import_succeeds():
    """Module imports without any hard Django dependency at the top level."""
    import sqlery.compat.rq as _rq

    expected = {
        "Queue",
        "Retry",
        "get_current_job",
        "JobStatus",
        "get_queue",
        "Job",
        "Worker",
        "NoSuchJobError",
        "get_job_registry_summary",
        "clear_failed_jobs",
        "delete_other_jobs_by_same_meta_tag",
        "is_final_retry",
        "get_queue_wait_time",
        "requeue_if_jobs_pending",
    }
    assert expected == set(_rq.__all__), f"__all__ mismatch: {set(_rq.__all__) ^ expected}"


def test_get_job_registry_summary_standalone(standalone_backend):
    """get_job_registry_summary buckets jobs by status correctly."""
    from sqlery.compat.rq import get_job_registry_summary

    standalone_backend._jobs = [
        FakeJob(id=1, status="running", queue_name="default"),
        FakeJob(id=2, status="success", queue_name="default"),
        FakeJob(id=3, status="failed", queue_name="default"),
        FakeJob(id=4, status="queued", queue_name="default", scheduled_at=datetime(2025, 1, 1, tzinfo=UTC)),
        FakeJob(id=5, status="queued", queue_name="default"),
    ]
    summary = get_job_registry_summary("default")

    assert set(summary.keys()) == {"started", "finished", "failed", "scheduled", "queued"}
    assert 1 in summary["started"]
    assert 2 in summary["finished"]
    assert 3 in summary["failed"]
    assert 4 in summary["scheduled"]
    assert 5 in summary["queued"]


def test_clear_failed_jobs_standalone(standalone_backend):
    """clear_failed_jobs returns the deleted count from backend.cleanup_jobs."""
    from sqlery.compat.rq import clear_failed_jobs

    count = clear_failed_jobs("default")
    assert count == 2  # MockBackend returns {"deleted": 2}


def test_delete_other_jobs_by_same_meta_tag_standalone(standalone_backend):
    """Only jobs with matching meta['tag'] (excluding current_job_id) are cancelled."""
    from sqlery.compat.rq import delete_other_jobs_by_same_meta_tag

    standalone_backend._jobs = [
        FakeJob(id=10, status="queued", meta={"tag": "import"}),   # current — excluded
        FakeJob(id=11, status="queued", meta={"tag": "import"}),   # match
        FakeJob(id=12, status="queued", meta={"tag": "export"}),   # different tag
        FakeJob(id=13, status="queued", meta=None),                 # no meta
        FakeJob(id=14, status="queued", meta={"tag": "import"}),   # match
    ]
    cancelled = delete_other_jobs_by_same_meta_tag(current_job_id=10, meta_tag="import")

    assert cancelled == 2
    assert 11 in standalone_backend.cancelled_ids
    assert 14 in standalone_backend.cancelled_ids
    assert 10 not in standalone_backend.cancelled_ids
    assert 12 not in standalone_backend.cancelled_ids


def test_get_queue_wait_time_empty_standalone(standalone_backend):
    """Returns 0 when queue is empty."""
    from sqlery.compat.rq import get_queue_wait_time

    standalone_backend._jobs = []
    wait = get_queue_wait_time("default")
    assert wait == 0


def test_get_queue_wait_time_nonempty_standalone(standalone_backend):
    """Returns positive integer when jobs exist in queue."""
    from sqlery.compat.rq import get_queue_wait_time

    standalone_backend._jobs = [
        FakeJob(id=1, status="queued", created_at=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC))
    ]
    wait = get_queue_wait_time("default")
    assert wait > 0


def test_worker_all_standalone(standalone_backend):
    """Worker.all() returns the list from get_worker_heartbeats."""
    from sqlery.compat.rq import Worker

    fake_workers = [{"worker_id": "w1", "status": "idle"}, {"worker_id": "w2", "status": "busy"}]
    standalone_backend._workers = fake_workers

    result = Worker.all()
    assert result == fake_workers


def test_job_fetch_not_found_standalone(standalone_backend):
    """Job.fetch raises NoSuchJobError when backend returns None."""
    from sqlery.compat.rq import Job, NoSuchJobError

    standalone_backend._job_by_id = None
    with pytest.raises(NoSuchJobError):
        Job.fetch(999)


def test_job_fetch_found_standalone(standalone_backend):
    """Job.fetch returns a Job wrapping the FakeJob from the backend."""
    from sqlery.compat.rq import Job

    fake = FakeJob(id=42, status="success")
    standalone_backend._job_by_id = fake

    job = Job.fetch(42)
    assert isinstance(job, Job)
    assert job.id == "42"
    assert job.get_status() == "success"
