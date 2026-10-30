"""#171 (finding C of #168): a worker that loses a claim race must not sleep a poll interval.

Contract, in plain English:

* A claim attempt can come back empty while work is queued: siblings hold the ready entries
  with ``FOR UPDATE SKIP LOCKED`` (a sibling locks a page of entries but claims one) or won
  the race for the head. A worker that has just worked (finished a job, or was woken by a
  NOTIFY) re-claims at once, a few bounded, jittered times, instead of waiting
  ``WORKER_POLL_INTERVAL`` for work that is already there. Shown end to end with a real
  worker whose next job is row-locked by another database session for a moment, exactly what
  a sibling mid-claim does, and with four real workers draining a backlog on PostgreSQL with
  ``WORKER_POLL_INTERVAL = 1.0``: the per-worker dispatch wait (start of a job minus the
  later of its enqueue and that worker's previous finish) has a p99 well under the poll
  interval.
* The quick retries are bounded and only follow work: a truly idle worker still issues the
  same queries and commits per idle second as before (#110 / #125 / #126).

PostgreSQL only: the lost race is a ``FOR UPDATE SKIP LOCKED`` skip and the wake-up a LISTEN /
NOTIFY, neither of which exists on SQLite. The retry loop and the ready probe are the same code
on both; SQLite's rarer compare-and-set loss is covered by the SQLite suite's claim tests.

Needs SQLERY_TEST_PG_URL (a superuser that may CREATE DATABASE); skipped otherwise.
"""

import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from itertools import pairwise
from pathlib import Path
from urllib.parse import urlparse

import pytest

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not os.environ.get("SQLERY_TEST_PG_URL"), reason="SQLERY_TEST_PG_URL not set"
    ),
]

ROOT = Path(__file__).resolve().parent.parent
WORKERS = 4
JOBS = 120
JOB_SECS = 0.02
HOLD = 0.03  # a sibling mid-claim holds the ready entries this long ...
HOLD_EVERY = 0.1  # ... this often
# Large enough that one poll sleep is unmistakable next to the per-job cost (fork, reconnect
# check, bookkeeping) of a slow, CPU-starved CI runner, which is tens of ms per dispatch.
POLL_INTERVAL = 2.0
MAX_DISPATCH_WAIT = 0.75 * POLL_INTERVAL
LOCK_AFTER_FINISH = 0.15  # the row lock outlives the previous job by this much
MAX_COMMITS_PER_POLL = 2  # per worker, the #110 idle bound

_DJANGO_SETUP = """
import json, os
from urllib.parse import urlparse
import django
from django.conf import settings
b = urlparse(os.environ["RACE171_DB_URL"])
settings.configure(
    DATABASES={"default": {"ENGINE": "django.db.backends.postgresql",
                           "NAME": b.path.lstrip("/"), "USER": b.username,
                           "PASSWORD": b.password or "", "HOST": b.hostname,
                           "PORT": b.port or 5432, "CONN_MAX_AGE": 600}},
    INSTALLED_APPS=["sqlery.django_sqlery"], USE_TZ=True,
    DEFAULT_AUTO_FIELD="django.db.models.BigAutoField", SECRET_KEY="race171",
    DJANGO_SQL_JOBS={"TRIGGER_MODE": "daemon", "ENABLE_DAEMON": False,
                     "WORKER_QUEUES": ["default"], "WORKER_POLL_INTERVAL": float(os.environ["RACE171_POLL"]),
                     "WORKER_HEARTBEAT_INTERVAL": 1, "SQLERY_PG_NOTIFY": os.environ.get("RACE171_NOTIFY") == "1"},
)
django.setup()
"""

_MIGRATE = (
    _DJANGO_SETUP + "from django.core.management import call_command\n"
    "call_command('migrate', verbosity=0)\n"
)

_WORKER = _DJANGO_SETUP + """
from sqlery.core.worker import WorkerProcess
# The parent reaps a finished child on a wait tick that backs off to WAIT_TICK_MAX (0.5 s); a
# test that must know when the post-job claim happens caps it so the reap lag is tiny.
WorkerProcess.WAIT_TICK_MAX = float(os.environ.get("RACE171_TICK", WorkerProcess.WAIT_TICK_MAX))
print("ready", flush=True)
WorkerProcess(queues=["default"]).run()
"""

_ENQUEUE = _DJANGO_SETUP + """
import time
from sqlery.core.job_queue import enqueue
for i in range(int(os.environ["RACE171_JOBS"])):
    enqueue("tests.test_claim_race_retry_171.record", allow_parallel=True, i=i, enqueued=time.time())
"""


def record(i, enqueued):
    """The job: note which worker ran it and when, in the file named by RACE171_LOG."""
    start = time.time()
    time.sleep(JOB_SECS)
    line = json.dumps(
        {"i": i, "worker": os.getppid(), "enqueued": enqueued, "start": start, "end": time.time()}
    )
    # One O_APPEND write per line: atomic across the worker processes.
    fd = os.open(os.environ["RACE171_LOG"], os.O_WRONLY | os.O_APPEND | os.O_CREAT)
    try:
        os.write(fd, (line + "\n").encode())
    finally:
        os.close(fd)


def slow(marker):
    """A job that runs for a while, then leaves a marker file as it finishes."""
    time.sleep(2.5)
    Path(marker).write_text("done")


_ENQUEUE_ONE = _DJANGO_SETUP + """
import time
from sqlery.core.job_queue import enqueue
enqueue(os.environ["RACE171_TASK"], allow_parallel=True, **json.loads(os.environ["RACE171_ARGS"]))
"""


def _connect(dbname):
    import psycopg

    base = urlparse(os.environ["SQLERY_TEST_PG_URL"])
    return psycopg.connect(
        host=base.hostname,
        port=base.port or 5432,
        user=base.username,
        password=base.password,
        dbname=dbname,
        autocommit=True,
    )


@pytest.fixture(scope="module")
def database():
    name = f"sqlery_race171_{uuid.uuid4().hex[:8]}"
    admin = _connect("postgres")
    admin.execute(f"CREATE DATABASE {name}")
    yield name
    admin.execute(f"DROP DATABASE {name} WITH (FORCE)")
    admin.close()


def _env(database, log):
    base = urlparse(os.environ["SQLERY_TEST_PG_URL"])
    url = f"postgresql://{base.username}@{base.hostname}:{base.port or 5432}/{database}"
    if base.password:
        url = url.replace(f"{base.username}@", f"{base.username}:{base.password}@")
    return {
        **os.environ,
        "PYTHONPATH": f"{ROOT / 'src'}{os.pathsep}{ROOT}",
        "RACE171_DB_URL": url,
        "RACE171_LOG": str(log),
        "RACE171_JOBS": str(JOBS),
        "RACE171_NOTIFY": "0",
        "RACE171_POLL": str(POLL_INTERVAL),
    }


def _run(script, env):
    r = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert r.returncode == 0, r.stderr[-800:]


class _Workers:
    def __init__(self, env, count):
        self.procs = [
            subprocess.Popen(
                [sys.executable, "-c", _WORKER],
                cwd=ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            for _ in range(count)
        ]

    def __enter__(self):
        for p in self.procs:
            assert p.stdout.readline().strip() == "ready"
        return self

    def __exit__(self, *exc):
        for p in self.procs:
            p.send_signal(signal.SIGTERM)
        for p in self.procs:
            try:
                p.wait(15)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(5)


def _migrate(database, env):
    admin = _connect(database)
    try:
        if not admin.execute("SELECT to_regclass('sqlery_queued_job')").fetchone()[0]:
            _run(_MIGRATE, env)
    finally:
        admin.close()


class _SiblingClaimer:
    """Emulates siblings mid-claim: every HOLD_EVERY seconds, locks all ready entries for HOLD
    seconds, as ``FOR UPDATE SKIP LOCKED`` claimers do while they decide and claim. A claim made
    in that window sees nothing although jobs are queued."""

    def __init__(self, database):
        self.conn = _connect(database)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self.stop.is_set():
            self.conn.execute("BEGIN")
            self.conn.execute("SELECT job_id FROM sqlery_job_ready FOR UPDATE").fetchall()
            time.sleep(HOLD)
            self.conn.execute("COMMIT")
            self.stop.wait(HOLD_EVERY - HOLD)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join(10)
        self.conn.close()


def _percentile(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * q))]


def _idle_commit_rate(database, windows=3, seconds=5):
    """Commits per second on the database in ``windows`` consecutive windows."""
    monitor = _connect("postgres")
    try:

        def commits():
            return monitor.execute(
                "SELECT xact_commit FROM pg_stat_database WHERE datname = %s", (database,)
            ).fetchone()[0]

        rates = []
        for _ in range(windows):
            c0, t0 = commits(), time.monotonic()
            time.sleep(seconds)
            rates.append((commits() - c0) / (time.monotonic() - t0))
        return rates
    finally:
        monitor.close()


def test_backlog_drains_without_sleeping_a_poll_interval_and_idle_cost_is_unchanged(
    database, tmp_path
):
    log = tmp_path / "jobs.log"
    env = _env(database, log)
    _migrate(database, env)

    # Four workers drain a backlog of short jobs while siblings (emulated) keep locking the
    # ready entries for a moment. NOTIFY is off, so only a re-claim (not a later wake-up) can
    # rescue a worker whose claim came back empty with jobs queued.
    with _Workers(env, WORKERS):
        time.sleep(3)  # registered, heartbeating, idle
        before = _idle_commit_rate(database, windows=2, seconds=4)
    _run(_ENQUEUE, env)
    with _Workers(env, WORKERS):
        with _SiblingClaimer(database):
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if log.exists() and len(log.read_text().splitlines()) >= JOBS:
                    break
                time.sleep(0.05)
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        assert len(rows) == JOBS
        wall = max(r["end"] for r in rows) - min(r["start"] for r in rows)

        # Dispatch wait per worker: how long a worker sat idle although the job was queued.
        # A worker's first job is skipped: that wait is the wake-up from a quiet queue,
        # not a lost race.
        waits = []
        by_worker = {}
        for r in sorted(rows, key=lambda r: r["start"]):
            by_worker.setdefault(r["worker"], []).append(r)
        assert len(by_worker) >= 2, "the backlog must be shared by several workers"
        for jobs in by_worker.values():
            for prev, cur in pairwise(jobs):
                waits.append(cur["start"] - max(cur["enqueued"], prev["end"]))
        # Waiting out a poll interval shows as a wait of >= POLL_INTERVAL; ordinary dispatch cost
        # (claim, fork, reconnect check) stays far below it even on a starved runner.
        slept = sorted(w for w in waits if w >= MAX_DISPATCH_WAIT)
        assert not slept, (
            f"{len(slept)} of {len(waits)} dispatches waited >= {MAX_DISPATCH_WAIT}s "
            f"(poll interval {POLL_INTERVAL}s): {[round(w, 2) for w in slept]}; "
            f"p99 {_percentile(waits, 0.99):.3f}s, wall {wall:.2f}s"
        )

        # Idle cost after the burst: the retries stop once the queue is empty, so the idle
        # commit rate is what it was before any work happened.
        time.sleep(3)
        after = _idle_commit_rate(database)
    # Judge the quietest window (an autovacuum visit adds commits to at most one).
    assert (
        min(after) <= MAX_COMMITS_PER_POLL * WORKERS / POLL_INTERVAL
    ), f"idle workers committed {[round(w, 1) for w in after]} per second"
    assert min(after) <= min(before) * 1.25 + 0.5, (
        f"idle commit rate rose from {[round(w, 1) for w in before]} to "
        f"{[round(w, 1) for w in after]} per second after a burst"
    )


def test_worker_whose_next_job_is_row_locked_by_a_sibling_reclaims_without_a_poll_sleep(
    database, tmp_path
):
    """A job is queued, but its ready entry is locked by another session when the worker
    finishes its previous job (SKIP LOCKED hides it). The lock is gone ~150 ms later. Before the
    fix the worker waited the whole poll interval; now it re-claims within a few retries."""
    log = tmp_path / "jobs.log"
    marker = tmp_path / "slow.done"
    env = {**_env(database, log), "RACE171_NOTIFY": "0", "RACE171_TICK": "0.02"}  # no NOTIFY
    _migrate(database, env)
    holder = _connect(database)
    try:
        with _Workers(env, 1):
            time.sleep(3)  # registered, heartbeating, idle
            _run(
                _ENQUEUE_ONE,
                {
                    **env,
                    "RACE171_TASK": "tests.test_claim_race_retry_171.slow",
                    "RACE171_ARGS": json.dumps({"marker": str(marker)}),
                },
            )
            # The worker finds the slow job within one poll; wait until it is running.
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if not holder.execute("SELECT count(*) FROM sqlery_job_ready").fetchone()[0]:
                    break
                time.sleep(0.05)
            _run(
                _ENQUEUE_ONE,
                {
                    **env,
                    "RACE171_TASK": "tests.test_claim_race_retry_171.record",
                    "RACE171_ARGS": json.dumps({"i": 1, "enqueued": time.time()}),
                },
            )
            holder.execute("BEGIN")
            locked = holder.execute(
                "SELECT job_id FROM sqlery_job_ready ORDER BY job_id FOR UPDATE"
            ).fetchall()
            assert len(locked) == 1, "exactly the second job is ready and now locked"
            # Hold the lock until the worker has recorded the slow job as finished, then a
            # while longer: the worker's next claim follows within a reap tick (20 ms), so it
            # always meets the locked entry, however slow the machine.
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if holder.execute(
                    "SELECT count(*) FROM sqlery_job_all WHERE status = 'success' "
                    "AND task_path LIKE '%%.slow'"
                ).fetchone()[0]:
                    break
                time.sleep(0.005)
            else:
                pytest.fail("the slow job never finished")
            assert marker.exists(), "the slow job must finish while the entry is locked"
            time.sleep(LOCK_AFTER_FINISH)
            holder.execute("COMMIT")  # the sibling's lock goes away; the entry is still ready
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not (log.exists() and log.read_text()):
                time.sleep(0.02)
            assert log.exists() and log.read_text(), "the locked job never ran"
    finally:
        holder.close()
    ran = json.loads(log.read_text().splitlines()[0])
    gap = ran["start"] - marker.stat().st_mtime
    assert gap < POLL_INTERVAL * 0.6, (
        f"the worker started the queued job {gap:.2f}s after it became free; "
        f"it slept a poll interval ({POLL_INTERVAL}s) instead of re-claiming"
    )
