"""#161: configurable worker pool, Celery-style prefork (default) or RQ-style fork-per-job.

Prefork hands each claimed job to one long-lived child, recycled after
WORKER_MAX_TASKS_PER_CHILD jobs; DB connections are refreshed at that same rate.
"""

import os
import signal
import time

import pytest

from sqlery.core import pool as pool_module
from sqlery.core.job import job
from sqlery.core.pool import (
    POOL_FORK,
    POOL_PREFORK,
    PoolSettings,
    db_refresh_due,
    resolve_pool_settings,
    should_recycle,
)
from sqlery.core.worker import WorkerProcess
from tests.pg_url import sqlalchemy_pg_url


@job
def whoami():
    return os.getpid()


@job
def boom():
    raise RuntimeError("task failed")


@job(max_retries=1, retry_backoff=0.1)
def fails_once_then_retried():
    raise RuntimeError("retry me")


@job
def nap(seconds):
    time.sleep(seconds)
    return "slept"


@job
def hard_crash():
    os._exit(3)


@job
def exits():
    raise SystemExit(0)


PG_URL = os.environ.get("SQLERY_TEST_PG_URL")
on_pg = [
    pytest.mark.postgres,
    pytest.mark.skipif(not PG_URL, reason="SQLERY_TEST_PG_URL not set"),
]


@pytest.fixture(params=["sqlite", pytest.param("pg", marks=on_pg)])
def backend(request, tmp_path):
    from sqlalchemy import text

    from sqlery import compat
    from sqlery.fastapi_sqlery import database
    from sqlery.fastapi_sqlery.backend import SQLAlchemyBackend

    database._engine = None
    if request.param == "pg":
        database.init_database(sqlalchemy_pg_url(PG_URL))
    else:
        database.init_database(f"sqlite:///{tmp_path / 'pool.db'}")
    engine = database.get_engine()
    b = SQLAlchemyBackend()
    compat._backend = b
    yield b
    compat._backend = None
    if request.param == "pg":
        with engine.connect() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
            conn.commit()
    engine.dispose()
    database._engine = None


@pytest.fixture
def make_worker(backend):
    workers = []

    def make(**settings):
        worker = WorkerProcess(queues=["default"], backend=backend)
        worker.pool_settings = resolve_pool_settings(**settings)
        workers.append(worker)
        return worker

    yield make
    for worker in workers:
        worker._stop_pool_child()


def run_next(backend, worker):
    """One iteration of the worker loop: claim the next job and run it in the pool."""
    claimed = backend.claim_job(["default"], worker.worker_id)
    assert claimed is not None
    return worker._execute_claimed(claimed)


def child_pids(backend, worker, count):
    pids = []
    for _ in range(count):
        whoami.enqueue()
        result = run_next(backend, worker)
        assert result["success"], result
        pids.append(int(result["output"]))
    return pids


# --- settings -----------------------------------------------------------------------------


def test_defaults_follow_celery(backend):
    """prefork pool, never recycled, no memory cap, DB refresh tied to the recycle rate."""
    worker = WorkerProcess(queues=["default"], backend=backend)
    assert worker.pool_settings == PoolSettings(POOL_PREFORK, None, None, None)


def test_db_reuse_defaults_to_recycle_rate():
    assert resolve_pool_settings(POOL_PREFORK, 50).db_reuse_max == 50
    assert resolve_pool_settings(POOL_PREFORK, 50, None, 10).db_reuse_max == 10
    assert resolve_pool_settings("FORK", 0, -1, "x") == PoolSettings(POOL_FORK)
    assert resolve_pool_settings("gevent").pool == POOL_PREFORK


def test_recycle_and_refresh_rules():
    assert not should_recycle(1000, None, None, None)
    assert should_recycle(2, 2, None, None)
    assert should_recycle(1, None, 300_000, 200_000)
    assert not should_recycle(1, None, 100_000, 200_000)
    assert [n for n in range(1, 7) if db_refresh_due(n, 3)] == [3, 6]
    assert not any(db_refresh_due(n, None) for n in range(1, 7))


def test_standalone_env_vars(monkeypatch):
    from sqlery.fastapi_sqlery.config import StandaloneConfig

    monkeypatch.setenv("SQLERY_WORKER_POOL", "fork")
    monkeypatch.setenv("SQLERY_WORKER_MAX_TASKS_PER_CHILD", "100")
    monkeypatch.setenv("SQLERY_WORKER_MAX_MEMORY_PER_CHILD", "200000")
    config = StandaloneConfig()
    assert config.get("WORKER_POOL") == "fork"
    assert config.get("WORKER_MAX_TASKS_PER_CHILD") == 100
    assert config.get("WORKER_MAX_MEMORY_PER_CHILD") == 200000
    assert config.get("WORKER_DB_REUSE_MAX") is None


# --- process model ------------------------------------------------------------------------


def test_prefork_runs_every_job_in_one_child(backend, make_worker):
    worker = make_worker()
    pids = child_pids(backend, worker, 4)
    assert len(set(pids)) == 1
    assert pids[0] != os.getpid()


def test_fork_pool_forks_a_child_per_job(backend, make_worker):
    worker = make_worker(pool=POOL_FORK)
    pids = child_pids(backend, worker, 3)
    assert len(set(pids)) == 3
    assert worker._pool_child is None


def test_prefork_child_recycled_after_max_tasks(backend, make_worker):
    worker = make_worker(max_tasks_per_child=2)
    a, b, c, d, e = child_pids(backend, worker, 5)
    assert a == b and c == d and len({a, c, e}) == 3
    with pytest.raises(ChildProcessError):
        os.waitpid(a, os.WNOHANG)  # the recycled child was reaped, not left a zombie


def test_prefork_child_recycled_on_memory(backend, make_worker, monkeypatch):
    monkeypatch.setattr("sqlery.core.worker.max_rss_kib", lambda: 10**9)
    worker = make_worker(max_memory_per_child=1)
    pids = child_pids(backend, worker, 2)
    assert pids[0] != pids[1]


def test_max_tasks_one_behaves_like_fork(backend, make_worker):
    worker = make_worker(max_tasks_per_child=1)
    assert len(set(child_pids(backend, worker, 3))) == 3


# --- outcomes -----------------------------------------------------------------------------


def test_failure_and_retry_keep_the_child(backend, make_worker):
    worker = make_worker()
    boom.enqueue()
    result = run_next(backend, worker)
    assert not result["success"] and "task failed" in result["error"]

    fails_once_then_retried.enqueue()
    result = run_next(backend, worker)
    assert not result["success"]
    time.sleep(0.2)  # the retry is due after its 0.1 s backoff
    retried = backend.claim_job(["default"], worker.worker_id)
    assert retried is not None and retried.retry_count == 1
    worker._execute_claimed(retried)

    first, second = child_pids(backend, worker, 2)
    assert first == second == worker._pool_child.pid


def test_timeout_in_child_disarms_the_alarm_and_keeps_the_child(backend, make_worker):
    worker = make_worker()
    before = child_pids(backend, worker, 1)[0]
    nap.enqueue(seconds=5, timeout_seconds=1)
    result = run_next(backend, worker)
    assert not result["success"] and "timeout" in result["error"]
    # Same child, and no alarm left armed to kill it during the next job.
    nap.enqueue(seconds=1.5)
    assert run_next(backend, worker)["success"]
    assert child_pids(backend, worker, 1)[0] == before


def test_crashed_child_marks_job_failed_and_is_replaced(backend, make_worker):
    worker = make_worker()
    before = child_pids(backend, worker, 1)[0]
    hard_crash.enqueue()
    result = run_next(backend, worker)
    assert not result["success"] and "status 3" in result["error"]
    assert worker._pool_child is None
    assert child_pids(backend, worker, 1)[0] != before


def test_system_exit_in_task_fails_the_job(backend, make_worker):
    worker = make_worker()
    exits.enqueue()
    result = run_next(backend, worker)
    assert not result["success"]
    assert child_pids(backend, worker, 1)


def test_revoke_kills_the_child_and_the_next_job_gets_a_new_one(backend, make_worker):
    from sqlery.core.result import REVOKED_ERROR

    worker = make_worker()
    before = child_pids(backend, worker, 1)[0]
    queued = nap.enqueue(seconds=30)
    claimed = backend.claim_job(["default"], worker.worker_id)
    backend.request_job_revoke(queued.id)
    result = worker._execute_claimed(claimed)
    assert result == {"success": False, "error": REVOKED_ERROR}
    assert child_pids(backend, worker, 1)[0] != before


def test_idle_child_killed_from_outside_is_replaced(backend, make_worker):
    worker = make_worker()
    before = child_pids(backend, worker, 1)[0]
    os.kill(before, signal.SIGKILL)
    time.sleep(0.1)
    assert child_pids(backend, worker, 1)[0] != before


def test_run_loop_stops_the_child_on_exit(backend, make_worker):
    worker = make_worker()
    for _ in range(3):
        whoami.enqueue()
    assert worker.run(max_jobs=3) == 3
    assert worker._pool_child is None


# --- DB connection refresh ----------------------------------------------------------------


@pytest.fixture
def refresh_log(tmp_path, monkeypatch):
    """Record every refresh_db_connections call, from the parent and the forked child."""
    log = tmp_path / "refresh.log"
    real = pool_module.refresh_db_connections

    def record(force):
        with open(log, "a") as f:
            f.write(f"{os.getpid()} {int(force)}\n")
        real(force)

    monkeypatch.setattr("sqlery.core.worker.refresh_db_connections", record)

    def forced_by(pid):
        if not log.exists():
            return 0
        rows = [line.split() for line in log.read_text().splitlines()]
        return sum(1 for p, force in rows if int(p) == pid and force == "1")

    return forced_by


def test_db_refreshed_at_the_recycle_rate(backend, make_worker, refresh_log):
    """WORKER_DB_REUSE_MAX unset follows WORKER_MAX_TASKS_PER_CHILD (here 3)."""
    worker = make_worker(max_tasks_per_child=3)
    for _ in range(6):
        whoami.enqueue()
    worker.run(max_jobs=6)
    # The parent force-refreshed after jobs 3 and 6; each child exits on its 3rd job
    # (its connections go with it) and never force-refreshed before that.
    assert refresh_log(os.getpid()) == 2


def test_db_reuse_max_overrides_the_rate(backend, make_worker, refresh_log):
    worker = make_worker(db_reuse_max=2)
    pids = child_pids(backend, worker, 4)
    assert refresh_log(pids[0]) == 2  # after jobs 2 and 4, child not recycled


def test_celery_default_never_forces_a_refresh(backend, make_worker, refresh_log):
    """No WORKER_MAX_TASKS_PER_CHILD / WORKER_DB_REUSE_MAX: only obsolete connections close."""
    worker = make_worker()
    pids = child_pids(backend, worker, 3)
    assert refresh_log(pids[0]) == 0


# --- connection loss ----------------------------------------------------------------------


def lost_connection():
    """What SQLAlchemy raises when psycopg finds the server gone."""
    import psycopg
    from sqlalchemy.exc import OperationalError

    return OperationalError(
        "SELECT 1", {}, psycopg.OperationalError("server closed the connection unexpectedly")
    )


def test_connection_loss_is_told_apart_from_statement_errors():
    import sqlite3

    import psycopg
    from django.db.utils import InterfaceError

    from sqlery.core.db_resilience import is_connection_lost

    assert is_connection_lost(lost_connection())
    assert is_connection_lost(psycopg.errors.AdminShutdown("terminating connection"))
    assert is_connection_lost(InterfaceError("connection already closed"))
    chained = RuntimeError("wrapped")
    chained.__cause__ = lost_connection()
    assert is_connection_lost(chained)
    assert not is_connection_lost(psycopg.errors.LockNotAvailable("could not obtain lock"))
    assert not is_connection_lost(psycopg.errors.QueryCanceled("statement timeout"))
    assert not is_connection_lost(sqlite3.OperationalError("database is locked"))
    assert not is_connection_lost(ValueError("task bug"))


def test_reconnect_backoff_is_exponential_capped_and_jittered():
    from sqlery.core.db_resilience import reconnect_delay

    ceilings = [reconnect_delay(n, 0.5, 4.0, rng=lambda: 1.0) for n in range(6)]
    assert ceilings == [0.5, 1.0, 2.0, 4.0, 4.0, 4.0]
    assert reconnect_delay(3, 0.5, 4.0, rng=lambda: 0.25) == 1.0
    assert all(0 <= reconnect_delay(n) <= 30 for n in range(100))


@pytest.fixture
def fast_reconnect(monkeypatch):
    monkeypatch.setattr("sqlery.core.worker.reconnect_backoff_settings", lambda: (0.01, 0.05))


@job
def counted(path):
    with open(path, "a") as f:
        f.write("ran\n")
    return "counted"


@job(max_retries=1, retry_backoff=0.1)
def counted_failure(path):
    with open(path, "a") as f:
        f.write("ran\n")
    raise RuntimeError("task failed")


def lose_connection_in_children(monkeypatch, backend, method):
    """``backend.<method>`` fails with a lost connection in any forked child, works in the parent."""
    parent = os.getpid()
    real = getattr(backend, method)

    def flaky(*args, **kwargs):
        if os.getpid() != parent:
            raise lost_connection()
        return real(*args, **kwargs)

    monkeypatch.setattr(backend, method, flaky)


@pytest.mark.parametrize("pool", [POOL_PREFORK, POOL_FORK])
def test_supervisor_persists_a_success_the_child_could_not(
    backend, make_worker, monkeypatch, tmp_path, fast_reconnect, pool
):
    """The job ran once; the parent records its result; nothing re-runs it."""
    runs = tmp_path / "runs"
    lose_connection_in_children(monkeypatch, backend, "mark_job_success")
    worker = make_worker(pool=pool)
    queued = counted.enqueue(path=str(runs))
    result = run_next(backend, worker)
    assert result["success"], result
    job_row = backend.get_job_by_id(queued.id)
    assert job_row.status == "success" and "counted" in job_row.output
    assert runs.read_text().count("ran") == 1
    assert backend.claim_job(["default"], worker.worker_id) is None  # nothing re-queued


@pytest.mark.parametrize("pool", [POOL_PREFORK, POOL_FORK])
def test_supervisor_persists_a_failure_and_its_retry(
    backend, make_worker, monkeypatch, tmp_path, fast_reconnect, pool
):
    runs = tmp_path / "runs"
    lose_connection_in_children(monkeypatch, backend, "mark_job_failed")
    worker = make_worker(pool=pool)
    queued = counted_failure.enqueue(path=str(runs))
    result = run_next(backend, worker)
    assert not result["success"]
    assert "task failed" in (backend.get_job_by_id(queued.id).error or "")
    assert backend.get_latest_retry(queued.id) is not None  # the task's own retry policy
    assert runs.read_text().count("ran") == 1


def test_prefork_child_survives_a_handed_over_outcome(
    backend, make_worker, monkeypatch, tmp_path, fast_reconnect
):
    lose_connection_in_children(monkeypatch, backend, "mark_job_success")
    worker = make_worker()
    counted.enqueue(path=str(tmp_path / "runs"))
    run_next(backend, worker)
    first = worker._pool_child.pid
    counted.enqueue(path=str(tmp_path / "runs"))
    run_next(backend, worker)
    assert worker._pool_child.pid == first


def test_parent_reconnects_with_backoff_to_read_the_result(
    backend, make_worker, monkeypatch, fast_reconnect
):
    real = backend.get_job_by_id
    failures = {"left": 2}
    discarded = []
    parent = os.getpid()

    def flaky(job_id):
        if os.getpid() == parent and failures["left"]:
            failures["left"] -= 1
            raise lost_connection()
        return real(job_id)

    worker = make_worker()
    whoami.enqueue()
    claimed = backend.claim_job(["default"], worker.worker_id)
    monkeypatch.setattr(backend, "get_job_by_id", flaky)
    monkeypatch.setattr("sqlery.core.worker.discard_connections", lambda: discarded.append(1))
    result = worker._execute_claimed(claimed)
    assert result["success"], result
    assert failures["left"] == 0 and len(discarded) == 2


def test_running_job_finishes_while_the_parent_cannot_reach_the_db(
    backend, make_worker, monkeypatch, fast_reconnect
):
    worker = make_worker()
    nap.enqueue(seconds=1.5)
    claimed = backend.claim_job(["default"], worker.worker_id)
    parent = os.getpid()
    for method in ("update_worker_heartbeat", "renew_queue_leases", "is_job_revoke_requested"):
        real = getattr(backend, method)

        def down(*args, _real=real, **kwargs):
            if os.getpid() == parent:
                raise lost_connection()
            return _real(*args, **kwargs)

        monkeypatch.setattr(backend, method, down)
    worker._owned_queues = {"default"}
    result = worker._execute_claimed(claimed)
    assert result["success"] and "slept" in result["output"]


def test_no_claims_while_the_database_is_unreachable(
    backend, make_worker, monkeypatch, fast_reconnect
):
    events = []
    real_claim = backend.claim_job

    def claim(queues, worker_id):
        events.append("claim")
        if events.count("claim") == 1:
            raise lost_connection()
        return real_claim(queues, worker_id)

    def probe():
        events.append("probe")
        if events.count("probe") < 3:
            raise lost_connection()

    monkeypatch.setattr(backend, "claim_job", claim)
    monkeypatch.setattr("sqlery.core.worker.probe_database", probe)
    whoami.enqueue()
    worker = make_worker()
    assert worker.run(max_jobs=1) == 1
    assert events[:5] == ["claim", "probe", "probe", "probe", "claim"]


@pytest.mark.postgres
@pytest.mark.skipif(not PG_URL, reason="SQLERY_TEST_PG_URL not set")
@pytest.mark.parametrize("backend", ["pg"], indirect=True)
def test_pg_parent_connection_terminated_during_the_job(backend, make_worker, tmp_path):
    """A real server-side kill of the parent's connections while the job runs."""
    runs = tmp_path / "runs"
    worker = make_worker()
    queued = kill_other_backends.enqueue(path=str(runs))
    result = run_next(backend, worker)
    assert result["success"], result
    assert backend.get_job_by_id(queued.id).status == "success"
    assert runs.read_text().count("ran") == 1


@job
def kill_other_backends(path):
    from sqlalchemy import text

    from sqlery.fastapi_sqlery import database

    with open(path, "a") as f:
        f.write("ran\n")
    with database.get_engine().connect() as conn:
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = current_database() AND pid <> pg_backend_pid()"
            )
        )
    return "killed"
