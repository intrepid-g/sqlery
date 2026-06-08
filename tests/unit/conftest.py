"""Shared fixtures and a FakeBackend for the framework-agnostic unit suite.

This conftest is scoped to `tests/unit/` so its `autouse` fixture does not
leak into the integration / Django-coupled suites at `tests/` and
`tests/integration/`.

The cornerstone is :class:`FakeBackend` — a strict subclass of
:class:`sqlery.compat.DatabaseBackend` that backs the entire abstract surface
with plain Python dicts. It is intentionally generous: methods that the three
unit modules don't currently exercise still ship a usable in-memory
implementation (rather than raising) so future tests can grow on top of it
without reopening this file.

Design notes
------------
* No Django, SQLAlchemy, or pytest-django imports here. The pytest invocation
  for the whole repo still configures Django (see ``pyproject.toml``
  ``[tool.pytest.ini_options]``), but the FakeBackend itself only touches
  ``sqlery.compat`` and the stdlib.
* Job/worker/task rows are represented as ``types.SimpleNamespace`` so that
  attribute access (``job.id``, ``job.tags``, ``job.status``) works the same
  way as the real ORM rows do in the production code paths.
* Side-effecting methods (``mark_job_*``, ``release_job``, etc.) return the
  mutated row instance so call sites that do ``job = backend.mark_job_*(...)``
  keep working.
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from sqlery.compat import DatabaseBackend
import sqlery.compat as _compat

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper factories — used by tests to seed FakeBackend state
# ---------------------------------------------------------------------------

_job_id_counter = itertools.count(1)
_task_id_counter = itertools.count(1)


def _next_job_id() -> int:
    return next(_job_id_counter)


def _next_task_id() -> int:
    return next(_task_id_counter)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def make_job(
    *,
    id: int | None = None,
    task_path: str = "tests.unit.fake.noop",
    queue_name: str = "default",
    status: str = "queued",
    priority: int = 0,
    tags: list[str] | None = None,
    dependencies: list[int] | None = None,
    kwargs: dict | None = None,
    max_retries: int = 0,
    retry_count: int = 0,
    retry_backoff: float = 1.0,
    timeout_seconds: int | None = None,
    allow_parallel: bool = True,
    ttl: int | None = None,
    created_at: datetime | None = None,
    scheduled_at: datetime | None = None,
    parent_job_id: int | None = None,
    job_name: str | None = None,
    version: int = 1,
    worker_pid: int | None = None,
    worker=None,
    started_at: datetime | None = None,
    output: str = "",
    error: str = "",
    **extra,
) -> SimpleNamespace:
    """Build a job row shaped like the real `QueuedJob` model.

    The returned object also exposes the ``mark_failed`` and
    ``check_dependencies_met`` methods that the claiming module calls
    directly on the row (some real implementations are model methods,
    not backend methods).
    """
    job = SimpleNamespace(
        id=id if id is not None else _next_job_id(),
        task_path=task_path,
        queue_name=queue_name,
        status=status,
        priority=priority,
        tags=tags or [],
        dependencies=dependencies or [],
        kwargs=kwargs if kwargs is not None else {},
        max_retries=max_retries,
        retry_count=retry_count,
        retry_backoff=retry_backoff,
        timeout_seconds=timeout_seconds,
        allow_parallel=allow_parallel,
        ttl=ttl,
        created_at=created_at or _utcnow(),
        scheduled_at=scheduled_at,
        parent_job_id=parent_job_id,
        job_name=job_name,
        version=version,
        worker_pid=worker_pid,
        worker=worker,
        started_at=started_at,
        finished_at=None,
        output=output,
        error=error,
        traceback="",
        termination_reason=None,
        **extra,
    )

    # Mimic model methods that core/claiming.py expects to call on the row.
    def mark_failed(error: str = "", termination_reason: str | None = None, **_kw):
        job.status = "failed"
        job.error = error
        job.termination_reason = termination_reason
        job.finished_at = _utcnow()
        return job

    job.mark_failed = mark_failed

    def check_dependencies_met():
        # Default helper — tests override per-case by reassigning the attr.
        return (True, [])

    job.check_dependencies_met = check_dependencies_met
    return job


def make_worker(
    *,
    worker_id: str = "worker_test_1",
    status: str = "idle",
    current_job=None,
    node_id: str = "test-node",
    pid: int = 1234,
    last_heartbeat: datetime | None = None,
    jobs_processed: int = 0,
) -> SimpleNamespace:
    worker = SimpleNamespace(
        worker_id=worker_id,
        status=status,
        current_job=current_job,
        current_job_id=current_job.id if current_job else None,
        node_id=node_id,
        pid=pid,
        last_heartbeat=last_heartbeat or _utcnow(),
        jobs_processed=jobs_processed,
        save=lambda update_fields=None: None,
    )
    return worker


def make_scheduled_task(
    *,
    id: int | None = None,
    name: str = "test-task",
    task_path: str = "tests.unit.fake.noop",
    cron_expression: str = "*/5 * * * *",
    queue_name: str = "default",
    priority: int = 0,
    enabled: bool = True,
    next_run_at: datetime | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=id if id is not None else _next_task_id(),
        name=name,
        task_path=task_path,
        cron_expression=cron_expression,
        queue_name=queue_name,
        priority=priority,
        enabled=enabled,
        next_run_at=next_run_at or _utcnow(),
    )


# ---------------------------------------------------------------------------
# FakeBackend — strict subclass of DatabaseBackend
# ---------------------------------------------------------------------------


class FakeBackend(DatabaseBackend):
    """In-memory :class:`DatabaseBackend` for the unit-test suite.

    Implements *every* abstract method on the ABC so Python's ABC machinery
    will let us instantiate the class. Methods used by the three unit modules
    receive proper implementations; methods that no unit test currently
    exercises ship a sensible default (usually a no-op or empty result) rather
    than raising, so the FakeBackend can be reused by future plans.
    """

    def __init__(self):
        # Core stores
        self._jobs: dict[int, SimpleNamespace] = {}
        self._workers: dict[str, SimpleNamespace] = {}
        self._scheduled_tasks: dict[int, SimpleNamespace] = {}
        self._leases: dict[str, dict] = {}
        self._registries: dict[str, set[int]] = {}
        # Tag bookkeeping
        self._tag_running: dict[str, int] = {}
        self._tag_started_history: list[tuple[str, datetime]] = []
        # Spy hooks — tests assert on these
        self.calls: list[tuple[str, tuple, dict]] = []

    # ----- internal helpers ---------------------------------------------
    def _record(self, name: str, *args, **kwargs) -> None:
        self.calls.append((name, args, kwargs))

    def add_job(self, job: SimpleNamespace) -> SimpleNamespace:
        """Test helper: insert a pre-built job row into the store."""
        self._jobs[job.id] = job
        return job

    def add_worker(self, worker: SimpleNamespace) -> SimpleNamespace:
        self._workers[worker.worker_id] = worker
        return worker

    def add_scheduled_task(self, task: SimpleNamespace) -> SimpleNamespace:
        self._scheduled_tasks[task.id] = task
        return task

    # ===== Abstract methods on DatabaseBackend ==========================
    # ---- @abstractmethod create_job
    def create_job(
        self,
        task_path: str,
        kwargs: dict,
        queue_name: str,
        priority: int,
        scheduled_at: datetime | None,
        max_retries: int,
        retry_backoff: float,
        allow_parallel: bool,
        timeout_seconds: int | None,
        retry_count: int | None = None,
        scheduled_task_id: int | None = None,
        job_name: str | None = None,
        retry_intervals: list | None = None,
        meta: dict | None = None,
        dependencies: list | None = None,
        on_success_path: str = "",
        on_failure_path: str = "",
        ttl: int | None = None,
        result_ttl: int | None = None,
        failure_ttl: int | None = None,
        parent_job_id: int | None = None,
    ):
        self._record("create_job", task_path, queue_name)
        job = make_job(
            task_path=task_path,
            kwargs=kwargs,
            queue_name=queue_name,
            priority=priority,
            scheduled_at=scheduled_at,
            max_retries=max_retries,
            retry_backoff=retry_backoff,
            allow_parallel=allow_parallel,
            timeout_seconds=timeout_seconds,
            retry_count=retry_count or 0,
            job_name=job_name,
            dependencies=dependencies or [],
            ttl=ttl,
            parent_job_id=parent_job_id,
        )
        return self.add_job(job)

    # ---- @abstractmethod claim_job
    def claim_job(self, queues: list[str], worker_id: str):
        self._record("claim_job", queues, worker_id)
        for job in self._jobs.values():
            if job.status == "queued" and job.queue_name in queues:
                job.status = "running"
                job.started_at = _utcnow()
                return job
        return None

    # ---- @abstractmethod get_queue_stats
    def get_queue_stats(self, queue_name: str | None = None) -> dict:
        stats: dict[str, int] = {}
        for job in self._jobs.values():
            if queue_name is not None and job.queue_name != queue_name:
                continue
            stats[job.status] = stats.get(job.status, 0) + 1
        return stats

    # ---- @abstractmethod cancel_job
    def cancel_job(self, job_id: int) -> bool:
        job = self._jobs.get(job_id)
        if not job or job.status != "queued":
            return False
        job.status = "cancelled"
        return True

    # ---- @abstractmethod retry_failed_jobs
    def retry_failed_jobs(self, queue_name: str | None = None, max_jobs: int | None = None) -> int:
        n = 0
        for job in list(self._jobs.values()):
            if job.status != "failed":
                continue
            if queue_name and job.queue_name != queue_name:
                continue
            job.status = "queued"
            n += 1
            if max_jobs and n >= max_jobs:
                break
        return n

    # ---- @abstractmethod get_due_scheduled_tasks
    def get_due_scheduled_tasks(self):
        now = _utcnow()
        return [
            t
            for t in self._scheduled_tasks.values()
            if t.enabled and t.next_run_at and t.next_run_at <= now
        ]

    # ---- @abstractmethod create_scheduled_task
    def create_scheduled_task(
        self,
        name: str,
        task_path: str,
        cron_expression: str,
        queue_name: str,
        priority: int,
        enabled: bool = True,
    ):
        task = make_scheduled_task(
            name=name,
            task_path=task_path,
            cron_expression=cron_expression,
            queue_name=queue_name,
            priority=priority,
            enabled=enabled,
        )
        return self.add_scheduled_task(task)

    # ---- @abstractmethod get_worker_heartbeats
    def get_worker_heartbeats(self, active_only: bool = True):
        now = _utcnow()
        out = []
        for w in self._workers.values():
            if active_only:
                age = (now - w.last_heartbeat).total_seconds()
                if age > 60:
                    continue
            out.append(w)
        return out

    # ---- @abstractmethod update_worker_heartbeat
    def update_worker_heartbeat(
        self, worker_id: str, status: str, current_job_id: int | None = None, **kwargs
    ):
        self._record("update_worker_heartbeat", worker_id, status, current_job_id)
        w = self._workers.get(worker_id)
        if w is None:
            w = make_worker(worker_id=worker_id, status=status)
            self._workers[worker_id] = w
        w.status = status
        w.current_job_id = current_job_id
        w.last_heartbeat = _utcnow()
        for k, v in kwargs.items():
            setattr(w, k, v)
        return w

    # ---- @abstractmethod cleanup_jobs
    def cleanup_jobs(
        self, status=None, max_age_days=None, max_count=None, queue_name=None, dry_run=False
    ) -> dict:
        return {"deleted": 0, "dry_run": dry_run}

    # ---- @abstractmethod cleanup_jobs_by_count
    def cleanup_jobs_by_count(
        self, status=None, keep_count=1000, queue_name=None, dry_run=False
    ) -> dict:
        return {"deleted": 0, "kept": min(len(self._jobs), keep_count), "dry_run": dry_run}

    # ---- @abstractmethod get_database_stats
    def get_database_stats(self) -> dict:
        return {"jobs": len(self._jobs), "workers": len(self._workers)}

    # ---- @abstractmethod vacuum_database
    def vacuum_database(self) -> dict:
        return {"vacuumed": True}

    # ---- @abstractmethod add_job_to_registry
    def add_job_to_registry(self, job_id: int, registry_type: str, metadata: dict | None = None):
        self._registries.setdefault(registry_type, set()).add(job_id)

    # ---- @abstractmethod remove_job_from_registry
    def remove_job_from_registry(self, job_id: int, registry_type: str):
        self._registries.get(registry_type, set()).discard(job_id)

    # ---- @abstractmethod get_registry_jobs
    def get_registry_jobs(
        self, registry_type: str, queue_name: str | None = None, limit: int | None = None
    ) -> list:
        ids = list(self._registries.get(registry_type, set()))
        jobs = [self._jobs[i] for i in ids if i in self._jobs]
        if queue_name:
            jobs = [j for j in jobs if j.queue_name == queue_name]
        if limit:
            jobs = jobs[:limit]
        return jobs

    # ---- @abstractmethod cleanup_registry
    def cleanup_registry(self, registry_type=None, max_age_days=None) -> dict:
        return {"deleted": 0}

    # ---- @abstractmethod get_job_by_id
    def get_job_by_id(self, job_id: int):
        return self._jobs.get(job_id)

    # ---- @abstractmethod mark_job_success
    def mark_job_success(self, job_id: int, output: str = ""):
        self._record("mark_job_success", job_id)
        job = self._jobs.get(job_id)
        if job is None:
            return None
        job.status = "success"
        job.output = output
        job.finished_at = _utcnow()
        return job

    # ---- @abstractmethod mark_job_failed
    def mark_job_failed(self, job_id: int, error: str, traceback: str = ""):
        self._record("mark_job_failed", job_id, error)
        job = self._jobs.get(job_id)
        if job is None:
            return None
        job.status = "failed"
        job.error = error
        job.traceback = traceback
        job.finished_at = _utcnow()
        return job

    # ---- @abstractmethod mark_job_archived
    def mark_job_archived(self, job_id: int):
        job = self._jobs.get(job_id)
        if job is not None:
            job.status = "archived"

    # ---- @abstractmethod cascade_ancestor_status
    def cascade_ancestor_status(self, job_id: int, status: str):
        # Walk parent chain upward and apply status.
        job = self._jobs.get(job_id)
        while job and getattr(job, "parent_job_id", None):
            parent = self._jobs.get(job.parent_job_id)
            if parent is None:
                break
            parent.status = status
            job = parent

    # ---- @abstractmethod has_pending_job_for_scheduled_task
    def has_pending_job_for_scheduled_task(self, task_id: int) -> bool:
        for j in self._jobs.values():
            if getattr(j, "scheduled_task_id", None) == task_id and j.status in (
                "queued",
                "running",
            ):
                return True
        return False

    # ---- @abstractmethod update_scheduled_task_next_run
    def update_scheduled_task_next_run(self, task_id: int, next_run_at: datetime):
        t = self._scheduled_tasks.get(task_id)
        if t:
            t.next_run_at = next_run_at

    # ---- @abstractmethod advance_scheduled_task_if_due
    def advance_scheduled_task_if_due(
        self,
        task_id: int,
        observed_next_run_at: datetime,
        new_next_run_at: datetime,
        job_kwargs: dict,
    ):
        """In-memory CAS on observed next_run_at, then enqueue in the same step.

        Mirrors the real backends: only the caller whose observed next_run_at
        still matches the row wins the advance and creates the job; concurrent
        losers get None. Single-threaded test backend, so the compare-and-swap
        is trivially atomic.
        """
        self._record("advance_scheduled_task_if_due", task_id)
        t = self._scheduled_tasks.get(task_id)
        if t is None:
            return None
        if getattr(t, "next_run_at", None) != observed_next_run_at:
            return None  # CAS lost — another caller already advanced
        t.next_run_at = new_next_run_at
        return self.create_job(**job_kwargs)

    # ---- @abstractmethod update_scheduled_task
    def update_scheduled_task(self, task_id: int, **updates):
        t = self._scheduled_tasks.get(task_id)
        if not t:
            return None
        for k, v in updates.items():
            setattr(t, k, v)
        return t

    # ---- @abstractmethod delete_scheduled_task
    def delete_scheduled_task(self, task_id: int) -> bool:
        return self._scheduled_tasks.pop(task_id, None) is not None

    # ---- @abstractmethod get_scheduled_tasks
    def get_scheduled_tasks(self, enabled_only: bool = False) -> list:
        tasks = list(self._scheduled_tasks.values())
        if enabled_only:
            tasks = [t for t in tasks if t.enabled]
        return tasks

    # ---- @abstractmethod get_scheduled_task
    def get_scheduled_task(self, task_id: int):
        return self._scheduled_tasks.get(task_id)

    # ---- @abstractmethod get_running_jobs
    def get_running_jobs(self, queue_name: str | None = None) -> list:
        out = [j for j in self._jobs.values() if j.status == "running"]
        if queue_name:
            out = [j for j in out if j.queue_name == queue_name]
        return out

    # ---- @abstractmethod get_running_jobs_for_liveness
    def get_running_jobs_for_liveness(self, queue_names=None) -> list:
        # Tests may inject crafted RunningJobLiveness records via
        # ``liveness_records``; otherwise default to an empty sweep.
        records = getattr(self, "liveness_records", None)
        if records is None:
            return []
        if queue_names is None:
            return list(records)
        # No queue metadata on injected records — return them unfiltered.
        return list(records)

    # ---- @abstractmethod fail_zombie_job
    def fail_zombie_job(self, job_id: int, reason: str) -> bool:
        self._record("fail_zombie_job", job_id, reason)
        job = self._jobs.get(job_id)
        if job is not None:
            job.status = "failed"
            job.error = reason
            job.termination_reason = "zombie_job"
        # Return True for crafted-record tests (job may not be in _jobs).
        return True

    # ---- @abstractmethod has_running_jobs_in_queue
    def has_running_jobs_in_queue(self, queue_name: str, exclude_job_id: int | None = None) -> bool:
        for j in self._jobs.values():
            if j.status == "running" and j.queue_name == queue_name and j.id != exclude_job_id:
                return True
        return False

    # ---- @abstractmethod release_job
    def release_job(self, job_id: int):
        job = self._jobs.get(job_id)
        if job is not None and job.status == "running":
            job.status = "queued"
            job.started_at = None

    # ---- @abstractmethod get_jobs
    def get_jobs(self, status=None, queue_name=None, limit=100, offset=0) -> list:
        out = list(self._jobs.values())
        if status:
            out = [j for j in out if j.status == status]
        if queue_name:
            out = [j for j in out if j.queue_name == queue_name]
        return out[offset : offset + limit]

    # ---- @abstractmethod count_jobs
    def count_jobs(self, status=None, queue_name=None) -> int:
        return len(self.get_jobs(status=status, queue_name=queue_name, limit=10**9))

    # ===== Concrete methods on ABC (with defaults) — overridden for testability ====

    def count_running_with_tag(self, tag: str) -> int:
        # Recompute from live state so tests can mutate _jobs directly.
        n = 0
        for j in self._jobs.values():
            if j.status == "running" and tag in (getattr(j, "tags", None) or []):
                n += 1
        return n + self._tag_running.get(tag, 0)

    def count_started_with_tag_since(self, tag: str, threshold: datetime) -> int:
        return sum(1 for (t, ts) in self._tag_started_history if t == tag and ts >= threshold)

    def get_expired_ttl_jobs(self) -> list:
        now = _utcnow()
        out = []
        for j in self._jobs.values():
            if j.status != "queued" or not j.ttl:
                continue
            if (now - j.created_at).total_seconds() > j.ttl:
                out.append(j)
        return out

    def acquire_tag_locks(self, tags: list[str]) -> None:
        self._record("acquire_tag_locks", tuple(tags))

    def get_claimable_jobs(self, queues, priority_weights=None, limit=1) -> list:
        out = [j for j in self._jobs.values() if j.status == "queued" and j.queue_name in queues]
        # Order by priority desc, then created_at asc.
        out.sort(key=lambda j: (-j.priority, j.created_at))
        # Optional queue weighting
        if priority_weights:
            out.sort(key=lambda j: -priority_weights.get(j.queue_name, 0))
        return out[:limit]

    def atomic_claim_job(self, job, worker) -> bool:
        # Reject if version mismatch flagged by test (set job._claim_should_fail).
        if getattr(job, "_claim_should_fail", False):
            return False
        live = self._jobs.get(job.id)
        if live is None or live.status != "queued":
            return False
        live.status = "running"
        live.started_at = _utcnow()
        live.worker = worker
        live.version += 1
        return True

    def claim_due_scheduled_task(self, task_id: int):
        return self._scheduled_tasks.get(task_id)

    def claim_queue_leases(self, queues, daemon_id, node_id, pid, lease_secs) -> list[str]:
        now = _utcnow()
        owned = []
        for q in queues:
            existing = self._leases.get(q)
            if existing and existing["expires_at"] > now and existing["daemon_id"] != daemon_id:
                continue
            self._leases[q] = {
                "daemon_id": daemon_id,
                "node_id": node_id,
                "pid": pid,
                "expires_at": now + timedelta(seconds=lease_secs),
            }
            owned.append(q)
        return owned

    def renew_queue_leases(self, owned_queues, daemon_id, lease_secs) -> None:
        now = _utcnow()
        for q in owned_queues:
            lease = self._leases.get(q)
            if lease and lease["daemon_id"] == daemon_id:
                lease["expires_at"] = now + timedelta(seconds=lease_secs)

    def release_queue_leases(self, owned_queues, daemon_id) -> None:
        for q in owned_queues:
            lease = self._leases.get(q)
            if lease and lease["daemon_id"] == daemon_id:
                self._leases.pop(q, None)

    def is_worker_paused(self, worker_id: str) -> bool:
        w = self._workers.get(worker_id)
        return bool(w and getattr(w, "paused", False))

    def update_job_child_pid(self, job_id: int, child_pid: int):
        job = self._jobs.get(job_id)
        if job is not None:
            job.worker_pid = child_pid

    def delete_worker_registration(self, worker_id: str) -> int:
        return 1 if self._workers.pop(worker_id, None) else 0

    def release_claimed_job(
        self, job, worker_id: str, status: str, jobs_processed: int = 0, **kwargs
    ):
        live = self._jobs.get(job.id)
        if live is not None:
            live.status = status
            for k, v in kwargs.items():
                setattr(live, k, v)
        w = self._workers.get(worker_id)
        if w is not None:
            w.status = "idle"
            w.current_job_id = None
            w.jobs_processed = jobs_processed

    def refresh_worker_heartbeat(self, worker_id):
        w = self._workers.get(worker_id)
        if w is not None:
            w.last_heartbeat = _utcnow()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_backend() -> FakeBackend:
    """Fresh FakeBackend per test."""
    return FakeBackend()


@pytest.fixture(autouse=True)
def _patch_get_backend(monkeypatch, fake_backend):
    """Auto-patched: `sqlery.compat.get_backend` returns the FakeBackend.

    Scoped to `tests/unit/` only because this fixture is defined in this
    conftest. Tests that need to call `get_backend()` directly inherit the
    in-memory backend.
    """
    monkeypatch.setattr(_compat, "get_backend", lambda: fake_backend)
    # Also reset the singleton so any module that captured the real backend
    # earlier in the process can be coaxed back to the fake via get_backend().
    monkeypatch.setattr(_compat, "_backend", fake_backend, raising=False)
    yield fake_backend
