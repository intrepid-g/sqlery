"""Shared chaos-test infrastructure: real-subprocess worker spawning + test-side enqueue.

Implements the "real-subprocess + Hypothesis" decision (CONTEXT D, Plan 03-06).
NOT used: ``multiprocessing.Process`` — see RESEARCH Pitfall #2 (forks under
pytest deadlock on held DB locks; local funcs aren't picklable).

Task functions are defined at MODULE level so worker subprocesses can import them
via the dotted path ``tests.chaos.conftest.<name>``.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from typing import Iterator

import pytest


# ---------------------------------------------------------------------------
# Module-level task functions
# ---------------------------------------------------------------------------
# All chaos task targets MUST be importable from the worker subprocess.
# Keep them module-level (RESEARCH Pitfall #3).


def task_succeeds(x: int = 1) -> int:
    """Return ``x * 2``. Smoke target for the happy-path chaos run.

    # ported from test_worker_chaos.py::fast_task (legacy API)
    """
    return x * 2


def task_sleeps(seconds: float = 10.0) -> None:
    """Sleep ``seconds`` so the parent worker can trip its timeout safety-net.

    # ported from test_worker_chaos.py::long_running_task (legacy API)
    """
    time.sleep(seconds)


def task_crashes() -> None:
    """Exit the child process with a non-zero status without raising.

    Exercises the parent's "child non-zero exit" branch, distinct from a
    normal Python exception.
    """
    os._exit(137)


def task_oom_signal() -> None:
    """SIGKILL self — simulates OOM-killer or external ``kill -9``.

    Exercises the daemon zombie-detection path (Check 1: PID gone).
    """
    os.kill(os.getpid(), signal.SIGKILL)


def task_increments_counter(path: str) -> None:
    """Append a single byte to ``path`` — verifies single-execution under
    concurrent-claim races. If two workers both run, the file is 2 bytes.
    """
    with open(path, "ab") as fh:
        fh.write(b"x")
        fh.flush()


def task_flaky(state_path: str, fail_first_n: int = 2) -> str:
    """Fail the first ``fail_first_n`` invocations using ``state_path`` as a counter.

    Used by retry/exponential-backoff tests. Counter is persisted across job
    invocations because each retry runs in a fresh subprocess.
    """
    try:
        attempts = int(open(state_path).read().strip() or "0")
    except (FileNotFoundError, ValueError):
        attempts = 0
    attempts += 1
    with open(state_path, "w") as fh:
        fh.write(str(attempts))
    if attempts <= fail_first_n:
        raise RuntimeError(f"flaky failure attempt={attempts}")
    return "ok"


# ---------------------------------------------------------------------------
# Subprocess helper
# ---------------------------------------------------------------------------


def spawn_worker(
    db_url: str,
    queue: str = "default",
    extra_args: list[str] | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.Popen:
    """Spawn a real worker subprocess via ``python -m sqlery.core.worker_runner``.

    The runner reads ``SQLERY_DATABASE_URL`` and ``WORKER_QUEUES`` from the
    environment (see ``src/sqlery/fastapi_sqlery/config.py``). We pass them via
    a fresh env so the parent pytest process is not polluted.

    ``start_new_session=True`` puts the child in its own process group so we
    can ``os.killpg`` it cleanly on teardown without taking down the test
    session.
    """
    env = os.environ.copy()
    env["SQLERY_DATABASE_URL"] = db_url
    # WORKER_QUEUES is not env-bound by StandaloneConfig; pass it via SQLERY_QUEUES
    # convention (a no-op if unknown — fall back to default). Worker reads
    # via get_config('WORKER_QUEUES', ['default']).
    env["SQLERY_QUEUES"] = queue
    # Avoid Django bootstrap inside the worker — we are testing standalone.
    env.pop("DJANGO_SETTINGS_MODULE", None)
    if extra_env:
        env.update(extra_env)

    args = [sys.executable, "-m", "sqlery.core.worker_runner"]
    if extra_args:
        args.extend(extra_args)

    return subprocess.Popen(
        args,
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )


def _terminate(proc: subprocess.Popen, grace: float = 5.0) -> None:
    """SIGTERM the process group; SIGKILL the survivors after ``grace`` seconds."""
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass


@contextmanager
def managed_workers(
    n: int,
    db_url: str,
    queue: str = "default",
    **spawn_kwargs,
) -> Iterator[list[subprocess.Popen]]:
    """Spawn ``n`` workers; guarantee teardown on exit (success or exception).

    Yields the list of Popen handles. Threats T-03-11, T-03-13.
    """
    procs: list[subprocess.Popen] = []
    try:
        for _ in range(n):
            procs.append(spawn_worker(db_url, queue=queue, **spawn_kwargs))
        yield procs
    finally:
        for p in procs:
            _terminate(p)


# ---------------------------------------------------------------------------
# Test-side enqueue helper (Plan-checker fix W2)
# ---------------------------------------------------------------------------


def enqueue(db_url: str, task_path: str, **kwargs):
    """Inject a job into the same SQLite file the worker subprocesses are reading.

    Opens a short-lived SQLAlchemy engine against ``db_url``, runs the standard
    ``SQLAlchemyBackend.create_job`` path, then disposes the engine so the
    file's WAL/locks are released before workers poll.

    Args:
        db_url: SQLite or Postgres URL — same value passed to ``spawn_worker``.
        task_path: Dotted path the worker will import,
            e.g. ``tests.chaos.conftest.task_sleeps``.
        **kwargs: Forwarded to ``create_job``. Common args:
            ``kwargs={...}``, ``queue_name="default"``, ``timeout_seconds=2``,
            ``max_retries=0``, ``priority=0``.

    Returns:
        The created ``QueuedJob`` row (with ``.id`` populated).
    """
    from sqlery.fastapi_sqlery import database as _db
    from sqlery.fastapi_sqlery.backend import SQLAlchemyBackend

    # Reset the module-global engine so each call gets a fresh engine
    # bound to the requested URL (test isolation).
    _db._engine = None
    _db.init_database(db_url)
    try:
        backend = SQLAlchemyBackend()
        # Fill required create_job args with chaos-suite defaults.
        defaults = {
            "kwargs": {},
            "queue_name": "default",
            "priority": 0,
            "scheduled_at": None,
            "max_retries": 0,
            "retry_backoff": 1.0,
            "allow_parallel": True,
            "timeout_seconds": None,
        }
        defaults.update(kwargs)
        return backend.create_job(task_path=task_path, **defaults)
    finally:
        try:
            _db.get_engine().dispose()
        except RuntimeError:
            pass
        _db._engine = None


def wait_for_status(db_url: str, job_id: int, statuses: set[str], timeout: float = 30.0):
    """Poll the job row until its status is in ``statuses`` or timeout elapses.

    Returns the final job row (or None on timeout). Uses a short-lived engine
    per poll so we never share a connection with the workers.
    """
    from sqlery.fastapi_sqlery import database as _db
    from sqlmodel import Session, select
    from sqlery.core.models import QueuedJob

    deadline = time.time() + timeout
    while time.time() < deadline:
        _db._engine = None
        _db.init_database(db_url)
        try:
            with Session(_db.get_engine()) as session:
                job = session.exec(select(QueuedJob).where(QueuedJob.id == job_id)).first()
                if job and job.status in statuses:
                    return job
        finally:
            try:
                _db.get_engine().dispose()
            except RuntimeError:
                pass
            _db._engine = None
        time.sleep(0.25)
    return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def chaos_db_url(tmp_path) -> str:
    """A per-test SQLite *file* URL (NOT ``:memory:`` — workers are separate procs).

    Returned in the standalone form ``sqlite:///<path>`` which both
    ``init_database`` and the worker_runner recognise.
    """
    return f"sqlite:///{tmp_path / 'chaos.db'}"
