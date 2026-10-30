"""#169: a worker keeps its own DB connection between jobs, whatever CONN_MAX_AGE says.

Contract, in plain English. With Django's default ``CONN_MAX_AGE = 0`` a daemon worker used to
open about two new database sessions per job (the main loop and the prefork child both ran
``close_old_connections()``, which closes a ``CONN_MAX_AGE = 0`` connection). Now:

* A worker (prefork parent and prefork child) that runs N jobs opens O(1) connections, not
  O(N). On PostgreSQL that is ``pg_stat_database.sessions`` and the backend pids the jobs see;
  on SQLite it is the identity of the child's connection (a TEMP table lives exactly as long
  as the connection that created it).
* The child never shares the parent's PostgreSQL backend (fork safety).
* A connection that breaks (here: ``pg_terminate_backend``) is still replaced, the worker
  goes on processing jobs and the worker parent itself reconnects (the #161 behaviour is
  unchanged).
* A job that leaves autocommit off (and a transaction open) does not hand that connection to
  the next job: Django's own autocommit check still applies, so no session stays
  ``idle in transaction``.

It runs for both pools (``WORKER_POOL`` prefork and fork-per-job) and both ways a worker
starts: "spawn" (a fresh interpreter, which is what ``WORKER_START_METHOD = "spawn"`` does) and
"fork" (the process forks the worker after Django is set up, which is what the launcher of
``WORKER_START_METHOD = "fork"`` does). Both end in the same ``WorkerProcess.run()``, which is
what owns the connections; the prefork child is always forked from that worker. The
fork-per-job pool forks a child per job and keeps its old per-job reconnect there, so only its
parent's loop is covered by the connection-count test (the child count does not apply).

End to end: a real worker subprocess (Django backend, ``CONN_MAX_AGE=0``, prefork pool, real
database). The PostgreSQL tests need SQLERY_TEST_PG_URL (a superuser that may CREATE DATABASE).
"""

import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import pytest

ROOT = Path(__file__).resolve().parent.parent
PG_URL = os.environ.get("SQLERY_TEST_PG_URL")
on_pg = pytest.mark.skipif(not PG_URL, reason="SQLERY_TEST_PG_URL not set")

_SETUP = """
import json, os
from urllib.parse import urlparse
import django
from django.conf import settings
if os.environ.get("C169_PG"):
    b = urlparse(os.environ["C169_PG"])
    db = {"ENGINE": "django.db.backends.postgresql", "NAME": b.path.lstrip("/"),
          "USER": b.username, "PASSWORD": b.password or "", "HOST": b.hostname,
          "PORT": b.port or 5432, "CONN_MAX_AGE": 0}
else:
    db = {"ENGINE": "django.db.backends.sqlite3", "NAME": os.environ["C169_SQLITE"],
          "CONN_MAX_AGE": 0}
settings.configure(
    DATABASES={"default": db}, INSTALLED_APPS=["sqlery.django_sqlery"], USE_TZ=True,
    DEFAULT_AUTO_FIELD="django.db.models.BigAutoField", SECRET_KEY="c169",
    DJANGO_SQL_JOBS={"TRIGGER_MODE": "daemon", "ENABLE_DAEMON": False,
                     "WORKER_QUEUES": ["default"], "WORKER_POLL_INTERVAL": 0.2,
                     "WORKER_HEARTBEAT_INTERVAL": 1, "SQLERY_PG_NOTIFY": False,
                     "WORKER_POOL": os.environ.get("C169_POOL", "prefork")},
)
django.setup()
"""

_MIGRATE = (
    _SETUP
    + "from django.core.management import call_command\ncall_command('migrate', verbosity=0)\n"
)

_ENQUEUE = _SETUP + """
from sqlery import enqueue
for _ in range(int(os.environ["C169_COUNT"])):
    enqueue("tests.test_worker_conn_reuse_169.probe", path=os.environ["C169_LOG"])
"""

_WORKER = _SETUP + """
import signal
from sqlery.core.worker import WorkerProcess
print("ready", flush=True)
if os.environ.get("C169_START") == "fork":
    # What the launcher does for WORKER_START_METHOD="fork": Django is set up, then the
    # worker is forked off and runs.
    pid = os.fork()
    if pid:
        signal.signal(signal.SIGTERM, lambda *_: os.kill(pid, signal.SIGTERM))
        while True:
            try:
                os.waitpid(pid, 0)
                break
            except InterruptedError:
                continue
        raise SystemExit(0)
WorkerProcess(queues=["default"]).run()
"""


def probe(path):
    """The job: report which process and which DB connection it ran on."""
    from django.db import connection

    with connection.cursor() as cur:
        if connection.vendor == "postgresql":
            cur.execute("SELECT to_regclass('pg_temp.c169') IS NOT NULL, pg_backend_pid()")
            exists, backend_pid = cur.fetchone()
        else:
            cur.execute("SELECT count(*) FROM sqlite_temp_master WHERE name = 'c169'")
            exists, backend_pid = cur.fetchone()[0] > 0, None
        if not exists:
            cur.execute("CREATE TEMP TABLE c169 (x integer)")
    with open(path, "a") as f:
        f.write(json.dumps({"pid": os.getpid(), "new_conn": not exists, "backend": backend_pid}))
        f.write("\n")


def leaky(path):
    """The job that misbehaves: leaves autocommit off with a transaction open."""
    from django.db import connection

    connection.set_autocommit(False)
    with connection.cursor() as cur:
        cur.execute("SELECT pg_backend_pid()" if connection.vendor == "postgresql" else "SELECT 1")
        backend_pid = cur.fetchone()[0]
    with open(path, "a") as f:
        f.write(json.dumps({"pid": os.getpid(), "leaky": True, "backend": backend_pid}) + "\n")


def _pg_conn(dbname):
    import psycopg

    b = urlparse(PG_URL)
    return psycopg.connect(
        host=b.hostname, port=b.port or 5432, user=b.username, password=b.password,
        dbname=dbname, autocommit=True,
    )  # fmt: skip


@pytest.fixture(params=["sqlite", pytest.param("pg", marks=on_pg)])
def env(request, tmp_path):
    base = {
        **os.environ,
        "PYTHONPATH": f"{ROOT / 'src'}{os.pathsep}{ROOT}",
        "C169_LOG": str(tmp_path / "log.jsonl"),
    }
    base.pop("C169_PG", None)
    if request.param == "sqlite":
        base["C169_SQLITE"] = str(tmp_path / "c169.db")
        _run(_MIGRATE, base)
        yield base, None
        return
    name = f"sqlery_c169_{uuid.uuid4().hex[:8]}"
    admin = _pg_conn("postgres")
    admin.execute(f"CREATE DATABASE {name}")
    b = urlparse(PG_URL)
    auth = b.username + (f":{b.password}" if b.password else "")
    base["C169_PG"] = f"postgresql://{auth}@{b.hostname}:{b.port or 5432}/{name}"
    _run(_MIGRATE, base)
    yield base, name
    admin.execute(f"DROP DATABASE {name} WITH (FORCE)")
    admin.close()


def _run(script, env, **extra):
    r = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, env={**env, **extra},
        capture_output=True, text=True, timeout=120, check=False,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr[-800:]


class _Worker:
    def __init__(self, env):
        self.proc = subprocess.Popen(
            [sys.executable, "-c", _WORKER], cwd=ROOT, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )  # fmt: skip

    def __enter__(self):
        assert self.proc.stdout.readline().strip() == "ready"
        return self

    def __exit__(self, *exc):
        self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(5)


def _records(env):
    path = Path(env["C169_LOG"])
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _wait_for(env, count, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        records = _records(env)
        if len(records) >= count:
            return records
        time.sleep(0.1)
    pytest.fail(f"only {len(_records(env))} of {count} jobs ran")


def _sessions(admin, database):
    return admin.execute(
        "SELECT sessions FROM pg_stat_database WHERE datname = %s", (database,)
    ).fetchone()[0]


VARIANTS = [
    pytest.param(("prefork", "spawn"), id="prefork-spawn"),
    pytest.param(("prefork", "fork"), id="prefork-fork"),
    pytest.param(("fork", "spawn"), id="forkpool-spawn"),
    pytest.param(("fork", "fork"), id="forkpool-fork"),
]


@pytest.fixture(params=VARIANTS)
def variant(request, env):
    pool, start = request.param
    env, database = env
    return {**env, "C169_POOL": pool, "C169_START": start}, database, pool


def test_worker_opens_a_constant_number_of_connections_at_conn_max_age_0(variant):
    env, database, pool = variant
    jobs = 30
    _run(_ENQUEUE, env, C169_COUNT=str(jobs))
    admin = _pg_conn("postgres") if database else None
    try:
        baseline = None
        with _Worker(env):
            if admin:
                baseline = _sessions(admin, database)
            records = _wait_for(env, jobs)
            if admin:
                opened = _sessions(admin, database) - baseline
                backends = {
                    r[0]
                    for r in admin.execute(
                        "SELECT pid FROM pg_stat_activity WHERE datname = %s "
                        "AND pid <> pg_backend_pid()",
                        (database,),
                    )
                }
    finally:
        if admin:
            admin.close()

    if pool == "fork":
        # A new child per job reconnects by design (fork safety); what #169 changes is the
        # parent loop, so only the parent's share of the sessions is bounded here.
        assert len({r["pid"] for r in records}) == jobs
        if database:
            assert opened <= 2 * jobs + 4, f"{opened} sessions opened for {jobs} jobs"
        return
    child_pids = {r["pid"] for r in records}
    assert len(child_pids) == 1, f"jobs ran in {len(child_pids)} children"
    new_conns = sum(r["new_conn"] for r in records)
    assert new_conns <= 2, f"the child opened {new_conns} connections for {jobs} jobs"
    if database:
        job_backends = {r["backend"] for r in records}
        assert len(job_backends) <= 2, f"jobs saw {len(job_backends)} backends"
        # Before the fix: about 2 sessions per job (>= 2 * jobs). Now: the child's, the
        # parent's and a little slack for the idle loop's other connections.
        assert opened <= 6, f"{opened} sessions opened while running {jobs} jobs"
        # Fork safety: the child's backend is not the one the parent holds.
        assert len(job_backends) == 1
        assert backends - job_backends, "the worker parent holds no backend of its own"


def test_broken_connection_is_replaced_and_jobs_keep_running(env):
    env, database = env
    if database is None:
        pytest.skip("terminating a backend needs PostgreSQL")
    first, second = 8, 8
    _run(_ENQUEUE, env, C169_COUNT=str(first))
    admin = _pg_conn("postgres")
    try:
        with _Worker(env):
            _wait_for(env, first)
            killed = admin.execute(
                "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (database,),
            ).fetchone()[0]
            assert killed >= 1
            _run(_ENQUEUE, env, C169_COUNT=str(second))
            records = _wait_for(env, first + second, timeout=90)
            # The parent was killed too: it must have reconnected (its heartbeat is written
            # through that connection) and it must hold a backend of its own again.
            deadline = time.monotonic() + 30
            while True:
                row = admin.execute(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = %s "
                    "AND pid <> pg_backend_pid() AND pid <> ALL(%s)",
                    (database, [r["backend"] for r in records[first:]]),
                ).fetchone()[0]
                beat = _heartbeat_age(env, database)
                if (row >= 1 and beat is not None and beat < 5) or time.monotonic() > deadline:
                    break
                time.sleep(0.5)
            assert row >= 1, "the worker parent did not reconnect after its backend was killed"
            assert beat is not None and beat < 5, f"worker heartbeat is stale ({beat}s)"
    finally:
        admin.close()
    after = records[first:]
    assert {r["backend"] for r in records[:first]}.isdisjoint({r["backend"] for r in after})
    assert len({r["backend"] for r in after}) <= 2


def _heartbeat_age(env, database):
    """Seconds since the newest worker heartbeat, read on a fresh connection."""
    conn = _pg_conn(database)
    try:
        cols = {
            r[0]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'sqlery_worker'"
            )
        }
        col = "last_heartbeat" if "last_heartbeat" in cols else None
        if col is None:
            pytest.fail(f"no heartbeat column in sqlery_worker: {sorted(cols)}")
        row = conn.execute(
            f"SELECT extract(epoch FROM now() - max({col})) FROM sqlery_worker"
        ).fetchone()
        return None if row[0] is None else float(row[0])
    finally:
        conn.close()


def test_job_leaving_autocommit_off_does_not_leak_its_connection(env):
    """The leaked connection is closed, not handed to the next job.

    The job's own outcome is written on that connection and is lost with it (as it was
    before #169), so afterwards only the connection is checked, not the queue: on PostgreSQL
    the leaked backend must be gone and no session may stay ``idle in transaction``; on SQLite
    the next job must run on a new connection.
    """
    env, database = env
    admin = _pg_conn("postgres") if database else None
    enqueue = (
        _SETUP
        + "from sqlery import enqueue\n"
        + "enqueue('tests.test_worker_conn_reuse_169.%s', path=os.environ['C169_LOG'])\n"
    )
    try:
        with _Worker(env):
            _run(enqueue % "probe", env)
            _run(enqueue % "leaky", env)
            records = _wait_for(env, 2)
            assert [("leaky" in r) for r in records] == [False, True], records
            if admin:
                leaked = records[1]["backend"]
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    alive = admin.execute(
                        "SELECT count(*) FROM pg_stat_activity WHERE pid = %s", (leaked,)
                    ).fetchone()[0]
                    if not alive:
                        break
                    time.sleep(0.2)
                assert not alive, "the connection a job left in a transaction was kept open"
                idle_in_txn = admin.execute(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = %s "
                    "AND state LIKE 'idle in transaction%%'",
                    (database,),
                ).fetchone()[0]
                assert idle_in_txn == 0, f"{idle_in_txn} session(s) idle in transaction"
            else:
                _run(enqueue % "probe", env)
                records = _wait_for(env, 3)
                assert records[2]["new_conn"], "the job after a leaky one reused its connection"
    finally:
        if admin:
            admin.close()
