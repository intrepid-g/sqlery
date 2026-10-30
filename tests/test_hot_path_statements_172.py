"""#172: the per-job hot path of a worker costs a handful of statements and commits.

Contract, in plain English. Running a noop job through a worker with the default prefork pool
used to take about 35 SQL statements and 15 PostgreSQL commits (37 statements on SQLite):
the claim, a duplicate "busy" heartbeat, the child re-reading the job it was just handed (and
once more to complete it), the child re-applying its session settings for every job, the
completion closing the attempt twice, and the parent reading the finished job back from the
database to learn what the child had just written. Now:

* A worker that runs N noop jobs one after the other, in a steady state, issues at most
  ``MAX_STATEMENTS_PER_JOB`` SQL statements per job in total (parent and child together, as
  seen by a connection-level execute hook; savepoints included), and on PostgreSQL commits at
  most ``MAX_COMMITS_PER_JOB`` transactions per job (``pg_stat_database.xact_commit``).
  Measured after the change: about 24 statements and 7 commits per job on PostgreSQL, about
  24.5 statements on SQLite. Before: about 35 and 15 (PostgreSQL), 37.4 (SQLite). The test's
  own polling is not counted among the commits (they are counted exactly and subtracted).
* None of it costs a guarantee. Every job still ends in ``sqlery_job_history`` as ``success``
  (or ``failed`` for a task that raises), its attempt is closed with that outcome and no
  attempt stays open (attempt fencing, #138), the history row records the worker, the
  child process that ran it and when (#140), and the worker goes on to the next job.

End to end: a real worker subprocess (Django backend, ``CONN_MAX_AGE=0``, prefork pool) over
a real database, counting in the worker processes themselves. The PostgreSQL test needs
SQLERY_TEST_PG_URL (a superuser that may CREATE DATABASE).
"""

import os
import signal
import sqlite3
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

JOBS = 100
WARMUP_JOBS = 5  # the first jobs pay for the child's start and cold caches
MAX_STATEMENTS_PER_JOB = 28  # measured ~24 (PostgreSQL) / ~24.5 (SQLite); was ~35 / ~37
MAX_COMMITS_PER_JOB = 10  # measured ~7; was ~15

_SETUP = """
import os
from urllib.parse import urlparse
import django
from django.conf import settings
if os.environ.get("H172_PG"):
    b = urlparse(os.environ["H172_PG"])
    db = {"ENGINE": "django.db.backends.postgresql", "NAME": b.path.lstrip("/"),
          "USER": b.username, "PASSWORD": b.password or "", "HOST": b.hostname,
          "PORT": b.port or 5432, "CONN_MAX_AGE": 0}
else:
    db = {"ENGINE": "django.db.backends.sqlite3", "NAME": os.environ["H172_SQLITE"],
          "CONN_MAX_AGE": 0}
settings.configure(
    DATABASES={"default": db}, INSTALLED_APPS=["sqlery.django_sqlery"], USE_TZ=True,
    DEFAULT_AUTO_FIELD="django.db.models.BigAutoField", SECRET_KEY="h172",
    DJANGO_SQL_JOBS={"TRIGGER_MODE": "daemon", "ENABLE_DAEMON": False,
                     "WORKER_QUEUES": ["default"], "WORKER_POLL_INTERVAL": 0.2,
                     "WORKER_HEARTBEAT_INTERVAL": 1, "SQLERY_PG_NOTIFY": False,
                     "WORKER_POOL": "prefork"},
)
django.setup()
if os.environ.get("H172_LOG"):
    # Count every statement any process of this worker sends, whatever opens the cursor.
    from django.db import connection

    def _count(execute, sql, params, many, context):
        with open(os.environ["H172_LOG"], "a") as f:
            f.write(f"{os.getpid()}\\n")
        return execute(sql, params, many, context)

    connection.execute_wrappers.append(_count)
"""

_MIGRATE = (
    _SETUP
    + "from django.core.management import call_command\ncall_command('migrate', verbosity=0)\n"
)

_ENQUEUE = _SETUP + """
from sqlery import enqueue
for _ in range(int(os.environ["H172_JOBS"])):
    enqueue(os.environ["H172_TASK"])
"""

_WORKER = _SETUP + """
from sqlery.core.worker import WorkerProcess
print("ready", flush=True)
WorkerProcess(queues=["default"]).run()
"""


def noop():
    return None


def boom():
    raise ValueError("boom")


def _pg_connect(dbname):
    import psycopg

    base = urlparse(PG_URL)
    return psycopg.connect(
        host=base.hostname,
        port=base.port or 5432,
        user=base.username,
        password=base.password,
        dbname=dbname,
        autocommit=True,
    )


class _Db:
    """A scratch database and the means to count in it, PostgreSQL or SQLite."""

    def __init__(self, kind, tmp_path):
        self.kind = kind
        self.probes = 0  # commits this test itself made in the counted database
        self.env = {
            **os.environ,
            "PYTHONPATH": f"{ROOT / 'src'}{os.pathsep}{ROOT}",
            "H172_LOG": str(tmp_path / "statements.log"),
        }
        self.log = Path(self.env["H172_LOG"])
        self.log.write_text("")
        if kind == "pg":
            self.name = f"sqlery_hot172_{uuid.uuid4().hex[:8]}"
            admin = _pg_connect("postgres")
            admin.execute(f"CREATE DATABASE {self.name}")
            admin.close()
            base = urlparse(PG_URL)
            url = f"postgresql://{base.username}@{base.hostname}:{base.port or 5432}/{self.name}"
            if base.password:
                url = url.replace(f"{base.username}@", f"{base.username}:{base.password}@")
            self.env["H172_PG"] = url
            self.conn = _pg_connect(self.name)
            self.stats = _pg_connect("postgres")
        else:
            self.env["H172_SQLITE"] = str(tmp_path / "hot172.sqlite3")
        self.run(_MIGRATE)

    def run(self, script, **extra):
        r = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env={**self.env, **extra},
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert r.returncode == 0, r.stderr[-1000:]

    def query(self, sql):
        if self.kind == "pg":
            self.probes += 1  # an autocommit statement is one commit in this very database
            if self.conn is None:
                self.conn = _pg_connect(self.name)
            return self.conn.execute(sql).fetchall()
        c = sqlite3.connect(self.env["H172_SQLITE"], timeout=30)
        try:
            return c.execute(sql).fetchall()
        finally:
            c.close()

    def finished(self):
        return self.query("SELECT count(*) FROM sqlery_job_history")[0][0]

    def commits(self):
        """Commits in the scratch database so far, the test's own polling not counted.

        Backends report their statistics when idle or on exit, so the polling connection is
        closed first and the worker must be stopped before the final reading.
        """
        if self.conn is not None:
            self.conn.close()
            self.conn = None
        return (
            self.stats.execute(
                "SELECT xact_commit FROM pg_stat_database WHERE datname = %s", (self.name,)
            ).fetchone()[0]
            - self.probes
        )

    def statements(self):
        return len(self.log.read_text().splitlines())

    def close(self):
        if self.kind == "pg":
            if self.conn is not None:
                self.conn.close()
            admin = self.stats
            admin.execute(f"DROP DATABASE {self.name} WITH (FORCE)")
            admin.close()


class _Worker:
    def __init__(self, env):
        self.proc = subprocess.Popen(
            [sys.executable, "-c", _WORKER],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )

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


def _wait_finished(db, count, timeout=90):
    deadline = time.monotonic() + timeout
    while db.finished() < count:
        assert time.monotonic() < deadline, f"only {db.finished()}/{count} jobs finished"
        time.sleep(0.05)


@pytest.fixture(params=["sqlite", pytest.param("pg", marks=[on_pg, pytest.mark.postgres])])
def db(request, tmp_path):
    database = _Db(request.param, tmp_path)
    yield database
    database.close()


def test_noop_jobs_cost_few_statements_and_commits(db):
    """#172: a steady stream of noop jobs stays under the statement and commit budget."""
    db.run(_ENQUEUE, H172_JOBS=str(JOBS), H172_TASK="tests.test_hot_path_statements_172.noop")
    commits0 = db.commits() if db.kind == "pg" else None
    with _Worker(db.env):
        _wait_finished(db, WARMUP_JOBS)
        statements0 = db.statements()
        done0 = db.finished()
        _wait_finished(db, JOBS)
        jobs = db.finished() - done0
        statements = db.statements() - statements0
    # Commits are read once the worker has exited (its statistics are then all reported) and
    # cover its whole life, start-up and shutdown included, spread over all the jobs.
    commits = db.commits() - commits0 if db.kind == "pg" else None
    assert jobs >= JOBS - WARMUP_JOBS - 5
    per_job = statements / jobs
    assert per_job <= MAX_STATEMENTS_PER_JOB, f"{per_job:.1f} statements per job"
    if commits is not None:
        assert commits / JOBS <= MAX_COMMITS_PER_JOB, f"{commits / JOBS:.1f} commits per job"


def test_cheaper_path_keeps_the_guarantees(db):
    """#172: every job is recorded, fenced and attributed as before, the worker keeps going."""
    db.run(_ENQUEUE, H172_JOBS="12", H172_TASK="tests.test_hot_path_statements_172.noop")
    db.run(_ENQUEUE, H172_JOBS="3", H172_TASK="tests.test_hot_path_statements_172.boom")
    with _Worker(db.env):
        _wait_finished(db, 15)
        time.sleep(0.5)  # the last child's closing statements
    statuses = dict(db.query("SELECT status, count(*) FROM sqlery_job_history GROUP BY status"))
    assert statuses == {"success": 12, "failed": 3}
    # Each job's one attempt is closed with the job's outcome; none is left open (#138).
    outcomes = dict(
        db.query(
            "SELECT outcome, count(*) FROM sqlery_job_attempt WHERE finished_at IS NOT NULL "
            "GROUP BY outcome"
        )
    )
    assert outcomes.get("success") == 12 and sum(outcomes.values()) == 15, outcomes
    assert db.query("SELECT count(*) FROM sqlery_job_attempt WHERE finished_at IS NULL")[0][0] == 0
    # The history row says who ran it: the worker and the child that executed it (#140).
    rows = db.query("SELECT worker_pid, child_pid, started_at, finished_at FROM sqlery_job_history")
    assert len(rows) == 15
    worker_pids = {r[0] for r in rows}
    child_pids = {r[1] for r in rows}
    assert len(worker_pids) == 1 and None not in worker_pids
    assert None not in child_pids and worker_pids.isdisjoint(child_pids)
    assert all(r[2] is not None and r[3] is not None for r in rows)
    # Nothing is left in the live table or the ready queue.
    assert db.query("SELECT count(*) FROM sqlery_queued_job")[0][0] == 0
    assert db.query("SELECT count(*) FROM sqlery_job_ready")[0][0] == 0
