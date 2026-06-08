"""Regression tests for Phase 1 standalone-import contract (UNIF-04/05/06).

These tests assert that `sqlery.core` and all its submodules import cleanly in a
Python interpreter where `django` has been forcibly blocked from `sys.modules`.

The implementation spawns a fresh subprocess and installs a `MetaPathFinder` that
raises `ImportError` on any attempt to import `django` or `django.*`. This works
even when `django` is installed in the dev environment (which it is, via the
`dev` extra). The CI job `standalone-no-django` provides the complementary
layer: a fresh venv where `django` is genuinely not installed.

If either layer ever fails, an unguarded `import django` has crept back into the
core layer — see CONTEXT.md "code_context" for the original 11 offending modules.
"""

import subprocess
import sys


# All 11 core submodules that must import cleanly without Django installed.
# Enumerated from CONTEXT.md / src/sqlery/core/ — keep in sync if new modules added.
_CORE_SUBMODULES = [
    "sqlery.core",
    "sqlery.core.claiming",
    "sqlery.core.worker",
    "sqlery.core.daemon",
    "sqlery.core.db_resilience",
    "sqlery.core.log_config",
    "sqlery.core.model_utils",
    "sqlery.core.daemon_runner",
    "sqlery.core.worker_runner",
    "sqlery.core.scheduler_tasks",
    "sqlery.core.utils",
    "sqlery.core.worker_pool",
]


_BLOCK_DJANGO_PREAMBLE = """
import sys


class _BlockDjango:
    def find_spec(self, name, path=None, target=None):
        if name == "django" or name.startswith("django."):
            raise ImportError(f"django blocked for test: {name}")
        return None


sys.meta_path.insert(0, _BlockDjango())
"""


def _run_in_subprocess(body: str, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run `body` in a fresh Python interpreter with django blocked at import time."""
    code = _BLOCK_DJANGO_PREAMBLE + body
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_core_imports_without_django():
    """All 11 sqlery.core submodules must import in a django-less interpreter."""
    imports = "\n".join(f"import {mod}" for mod in _CORE_SUBMODULES)
    body = imports + '\nprint("OK")\n'

    result = _run_in_subprocess(body)

    assert result.returncode == 0, (
        f"Subprocess failed (rc={result.returncode}).\n"
        f"stdout={result.stdout}\n"
        f"stderr={result.stderr}"
    )
    assert "OK" in result.stdout, f"Missing OK sentinel. stdout={result.stdout!r}"


def test_db_resilience_retry_works_without_django():
    """`retry_on_db_error` decorator must wrap functions without Django installed.

    This proves Plan 01 Task 1's `_RETRYABLE_EXC` fallback path actually works
    at runtime when django.db.utils is unavailable.
    """
    body = """
import sqlery.core.db_resilience as dbr

assert hasattr(dbr, "retry_on_db_error"), "retry_on_db_error missing"

@dbr.retry_on_db_error(max_retries=1)
def _noop():
    return 42

assert _noop() == 42, "decorated function did not return underlying value"
print("RETRY_OK")
"""
    result = _run_in_subprocess(body)

    assert result.returncode == 0, (
        f"Subprocess failed (rc={result.returncode}).\n"
        f"stdout={result.stdout}\n"
        f"stderr={result.stderr}"
    )
    assert "RETRY_OK" in result.stdout, f"Missing RETRY_OK sentinel. stdout={result.stdout!r}"


# ---------------------------------------------------------------------------
# Standalone SQLAlchemyBackend.advance_scheduled_task_if_due correctness (Phase 10)
#
# DB-correctness proof of the atomic advance primitive (Plan 01) on the standalone
# SQLAlchemy backend, against a real per-test temp-file SQLite engine. This is the
# standalone counterpart to the Django-mode behavioral tests in
# tests/test_atomic_scheduler.py; the full {Django, standalone} x {SQLite, Postgres}
# parity matrix is deferred to Phase 11. Asserted directly against the backend (no
# Scheduler needed): a winning CAS creates a job and advances next_run_at; a stale
# observed_due returns None and creates no job; two calls with the same observed_due
# fire exactly once.
# ---------------------------------------------------------------------------

from datetime import datetime, timedelta, UTC

import pytest


@pytest.fixture
def standalone_backend(tmp_path, monkeypatch):
    """Build a fresh SQLAlchemyBackend against a per-test temp-file SQLite engine.

    Mirrors the proven fixture in tests/unit/test_sqlalchemy_backend_sync.py:
    create_all populates the schema from SQLModel.metadata, and the module-level
    _engine is monkeypatched so SQLAlchemyBackend picks it up via get_session.
    """
    from sqlalchemy import create_engine
    from sqlmodel import SQLModel

    from sqlery.fastapi_sqlery import database as db_mod

    # Importing core.models populates SQLModel.metadata (used by create_all).
    from sqlery.core import models as _core_models  # noqa: F401

    db_path = tmp_path / "db.sqlite"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)

    monkeypatch.setattr(db_mod, "_engine", engine, raising=False)

    from sqlery.fastapi_sqlery.backend import SQLAlchemyBackend

    backend = SQLAlchemyBackend()
    try:
        yield backend
    finally:
        engine.dispose()


def _make_due_scheduled_task(backend, *, name="cron-standalone", past_minutes=1):
    """Create a cron ScheduledTask with next_run_at pinned to the recent past."""
    task = backend.create_scheduled_task(
        name=name,
        task_path="tests.fake.task",
        cron_expression="*/5 * * * *",
        queue_name="default",
        priority=5,
    )
    due = datetime.now(UTC) - timedelta(minutes=past_minutes)
    backend.update_scheduled_task_next_run(task.id, due)
    return backend.get_scheduled_task(task.id)


def _job_kwargs_for(task):
    """Build advance/create_job-style job_kwargs per 10-01-SUMMARY field mapping."""
    return {
        "task_path": task.task_path,
        "kwargs": {},
        "queue_name": task.queue_name,
        "priority": task.priority,
        "scheduled_at": None,
        "max_retries": 0,
        "retry_backoff": 1.0,
        "allow_parallel": False,
        "timeout_seconds": None,
        "scheduled_task_id": task.id,
    }


def _count_jobs_for(backend, task_id):
    """Count QueuedJob rows for a scheduled task via the backend's own session."""
    from sqlmodel import select

    from sqlery.core.models import QueuedJob

    with backend._get_session() as session:
        rows = session.exec(select(QueuedJob).where(QueuedJob.scheduled_task_id == task_id)).all()
        return len(list(rows))


class TestStandaloneAdvanceScheduledTask:
    """Direct DB-correctness tests for SQLAlchemyBackend.advance_scheduled_task_if_due."""

    def test_winning_cas_creates_job_and_advances(self, standalone_backend):
        """A matching observed_due wins the CAS: returns a job, advances, one job."""
        backend = standalone_backend
        task = _make_due_scheduled_task(backend, name="cron-win")
        observed_due = task.next_run_at
        new_next_run = datetime.now(UTC) + timedelta(minutes=5)

        job = backend.advance_scheduled_task_if_due(
            task.id, observed_due, new_next_run, _job_kwargs_for(task)
        )

        assert job is not None
        assert job.scheduled_task_id == task.id
        assert _count_jobs_for(backend, task.id) == 1

        advanced = backend.get_scheduled_task(task.id)
        # SQLite returns naive datetimes; compare on the wall value.
        stored = advanced.next_run_at
        stored = stored if stored.tzinfo else stored.replace(tzinfo=UTC)
        assert stored == new_next_run

    def test_stale_observed_due_returns_none_no_job(self, standalone_backend):
        """A non-matching observed_due loses the CAS: returns None, no job created."""
        backend = standalone_backend
        task = _make_due_scheduled_task(backend, name="cron-stale")
        # An observed_due that does NOT match the row's current next_run_at.
        stale_observed = datetime.now(UTC) - timedelta(days=999)
        new_next_run = datetime.now(UTC) + timedelta(minutes=5)

        job = backend.advance_scheduled_task_if_due(
            task.id, stale_observed, new_next_run, _job_kwargs_for(task)
        )

        assert job is None
        assert _count_jobs_for(backend, task.id) == 0
        # The row was not advanced.
        unchanged = backend.get_scheduled_task(task.id)
        stored = unchanged.next_run_at
        stored = stored if stored.tzinfo else stored.replace(tzinfo=UTC)
        assert stored != new_next_run

    def test_two_attempts_same_observed_due_fire_exactly_once(self, standalone_backend):
        """Two advances with the same observed_due: first wins, second is None, one job."""
        backend = standalone_backend
        task = _make_due_scheduled_task(backend, name="cron-once")
        observed_due = task.next_run_at
        new_next_run = datetime.now(UTC) + timedelta(minutes=5)
        job_kwargs = _job_kwargs_for(task)

        first = backend.advance_scheduled_task_if_due(
            task.id, observed_due, new_next_run, job_kwargs
        )
        second = backend.advance_scheduled_task_if_due(
            task.id, observed_due, new_next_run, job_kwargs
        )

        assert first is not None
        assert second is None
        assert _count_jobs_for(backend, task.id) == 1

        advanced = backend.get_scheduled_task(task.id)
        stored = advanced.next_run_at
        stored = stored if stored.tzinfo else stored.replace(tzinfo=UTC)
        assert stored == new_next_run
