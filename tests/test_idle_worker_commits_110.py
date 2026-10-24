"""#110: an idle worker committed ~5 transactions on every poll.

End-to-end against a real PostgreSQL scratch database: ``migrate`` creates sqlery's tables,
a real ``WorkerProcess`` subprocess (Django backend, poll interval 1 s, NOTIFY off,
``CONN_MAX_AGE=600``) idles, and ``pg_stat_database.xact_commit`` is read over a window of
polls. Before the fix every idle poll ran a lease renewal, a scheduled-task lookup, a claim
attempt, a heartbeat and a next-delayed-job lookup, each its own transaction (5 per poll).

Also proves the reductions stay safe: the scheduler lease never lapses while the worker
holds it, and a due scheduled task is still enqueued on time.

Needs SQLERY_TEST_PG_URL (a superuser that may CREATE DATABASE); skipped otherwise.
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

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not os.environ.get("SQLERY_TEST_PG_URL"), reason="SQLERY_TEST_PG_URL not set"
    ),
]

POLLS_PER_WINDOW = 5  # idle polls per observation window (WORKER_POLL_INTERVAL = 1 s)
WINDOWS = 3
MAX_COMMITS_PER_POLL = 2
ROOT = Path(__file__).resolve().parent.parent

_DJANGO_SETUP = """
import json, os
from urllib.parse import urlparse
import django
from django.conf import settings
b = urlparse(os.environ["IDLE110_DB_URL"])
settings.configure(
    DATABASES={"default": {"ENGINE": "django.db.backends.postgresql",
                           "NAME": b.path.lstrip("/"), "USER": b.username,
                           "PASSWORD": b.password or "", "HOST": b.hostname,
                           "PORT": b.port or 5432, "CONN_MAX_AGE": 600}},
    INSTALLED_APPS=["sqlery.django_sqlery"], USE_TZ=True,
    DEFAULT_AUTO_FIELD="django.db.models.BigAutoField", SECRET_KEY="idle110",
    DJANGO_SQL_JOBS=dict({"TRIGGER_MODE": "daemon", "ENABLE_DAEMON": False,
                          "WORKER_QUEUES": ["default"], "WORKER_POLL_INTERVAL": 1,
                          "WORKER_HEARTBEAT_INTERVAL": 1, "SQLERY_PG_NOTIFY": False},
                         **json.loads(os.environ.get("IDLE110_SETTINGS", "{}"))),
)
django.setup()
"""

_MIGRATE = (
    _DJANGO_SETUP + "from django.core.management import call_command\n"
    "call_command('migrate', verbosity=0)\n"
)

_WORKER = _DJANGO_SETUP + """
from sqlery.core.worker import WorkerProcess
print("ready", flush=True)
WorkerProcess(queues=["default"]).run()
"""

_ADD_TASK = _DJANGO_SETUP + """
from datetime import timedelta
from django.utils import timezone
from sqlery.django_sqlery.models import ScheduledTask
due = timezone.now() + timedelta(seconds=float(os.environ["IDLE110_DUE_IN"]))
ScheduledTask.objects.bulk_create([ScheduledTask(
    name=name, task_path=path, schedule_type="cron", cron_expression="0 0 1 1 *",
    queue_name="default", next_run_at=due)
    for name, path in json.loads(os.environ["IDLE110_TASKS"])])
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
    name = f"sqlery_idle110_{uuid.uuid4().hex[:8]}"
    admin = _connect("postgres")
    admin.execute(f"CREATE DATABASE {name}")
    yield name
    admin.execute(f"DROP DATABASE {name} WITH (FORCE)")
    admin.close()


def _env(database, settings=None):
    base = urlparse(os.environ["SQLERY_TEST_PG_URL"])
    url = f"postgresql://{base.username}@{base.hostname}:{base.port or 5432}/{database}"
    if base.password:
        url = url.replace(f"{base.username}@", f"{base.username}:{base.password}@")
    return {
        **os.environ,
        "PYTHONPATH": f"{ROOT / 'src'}{os.pathsep}{ROOT}",
        "IDLE110_DB_URL": url,
        "IDLE110_SETTINGS": json.dumps(settings or {}),
    }


def _run(script, env, **extra):
    r = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env={**env, **extra},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert r.returncode == 0, r.stderr[-800:]


def _migrate(database, env):
    admin = _connect(database)
    try:
        has_tables = admin.execute("SELECT to_regclass('sqlery_queued_job')").fetchone()[0]
    finally:
        admin.close()
    if not has_tables:
        _run(_MIGRATE, env)


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


def test_idle_worker_commits_at_most_two_transactions_per_poll(database):
    """#110: one idle worker commits at most 2 transactions per poll (was 5)."""
    env = _env(database)
    _migrate(database, env)
    with _Worker(env):
        time.sleep(4)  # registration, first heartbeat, caches warm
        admin = _connect("postgres")
        try:

            def count():
                row = admin.execute(
                    "SELECT xact_commit FROM pg_stat_database WHERE datname = %s", (database,)
                ).fetchone()
                return row[0]

            # pg_stat_database.xact_commit counts EVERY backend on the database, not just the
            # worker. In particular an autovacuum worker visiting the freshly migrated scratch
            # database (default autovacuum_naptime = 1 min, so it lands anywhere, e.g. in CI
            # after the rest of the postgres suite) commits ~14 transactions of its own
            # (VACUUM/ANALYZE per catalog and sqlery table). A steady worker commits in every
            # window, a one-off visitor in at most one, so measure several short windows
            # and judge the quietest: a worker over the bound fails all of them.
            windows = []
            for _ in range(WINDOWS):
                c0, t0 = count(), time.monotonic()
                time.sleep(POLLS_PER_WINDOW)
                c1, t1 = count(), time.monotonic()
                windows.append((c1 - c0, t1 - t0))
        finally:
            admin.close()
    rates = [commits / elapsed for commits, elapsed in windows]
    assert min(rates) <= MAX_COMMITS_PER_POLL, (
        f"an idle worker committed {[c for c, _ in windows]} transactions in "
        f"{POLLS_PER_WINDOW} polls each = {[round(r, 1) for r in rates]} per poll"
    )


def test_idle_worker_keeps_lease_and_fires_due_task(database):
    """#110: the cheaper idle poll still holds the scheduler lease and enqueues on time.

    The lease TTL is 3 x the poll interval (3 s). Over several TTLs the lease row must
    never be expired while the worker is alive, and a scheduled task due mid-run must
    be enqueued within a couple of polls of its due time.
    """
    # Recheck every 3 s and add the task due at t=9 s, i.e. AFTER the worker's next recheck,
    # so firing on time depends on the worker scheduling the known due time, not on luck.
    env = _env(database, {"SCHEDULER_RECHECK_INTERVAL": 3})
    _migrate(database, env)
    admin = _connect(database)
    try:
        admin.execute("DELETE FROM sqlery_queued_job")
        admin.execute("DELETE FROM sqlery_scheduled_task")
        admin.execute("DELETE FROM sqlery_daemon_lease")
        with _Worker(env):
            time.sleep(3)
            _run(
                _ADD_TASK,
                env,
                IDLE110_DUE_IN="9",
                IDLE110_TASKS=json.dumps([["idle110", "tests.test_idle_worker_commits_110.noop"]]),
            )
            created = time.monotonic()
            lapses, fired_at, samples = [], None, 0
            while time.monotonic() - created < 13:
                row = admin.execute(
                    "SELECT expires_at > now() FROM sqlery_daemon_lease "
                    "WHERE queue_name = 'default'"
                ).fetchone()
                samples += 1
                if row is None or not row[0]:
                    lapses.append(round(time.monotonic() - created, 1))
                if (
                    fired_at is None
                    and admin.execute("SELECT count(*) FROM sqlery_queued_job").fetchone()[0]
                ):
                    fired_at = time.monotonic() - created
                time.sleep(0.1)
    finally:
        admin.close()
    assert samples > 30
    assert not lapses, f"scheduler lease lapsed at t={lapses}"
    assert fired_at is not None, "due scheduled task was never enqueued"
    assert 8.5 <= fired_at <= 9 + 2.5, f"task due at t=9 was enqueued at t={fired_at:.1f}"


_POISON = """
CREATE TABLE idle110_fail (on_ boolean);
CREATE FUNCTION idle110_poison_job() RETURNS trigger AS $$
BEGIN
    IF NEW.task_path = 'tests.test_idle_worker_commits_110.poison' THEN
        RAISE EXCEPTION 'idle110: injected enqueue error';
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE FUNCTION idle110_poison_worker() RETURNS trigger AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM idle110_fail) THEN
        RAISE EXCEPTION 'idle110: injected heartbeat error';
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER idle110_poison_job BEFORE INSERT ON sqlery_queued_job
    FOR EACH ROW EXECUTE FUNCTION idle110_poison_job();
CREATE TRIGGER idle110_poison_worker BEFORE UPDATE ON sqlery_worker
    FOR EACH ROW EXECUTE FUNCTION idle110_poison_worker();
"""


def test_idle_worker_survives_database_errors_in_idle_poll(database):
    """#110: a swallowed database error inside the idle poll must not undo other work.

    The idle poll shares one transaction, and the heartbeat and the per-task scheduler
    loop swallow their own errors. Real PostgreSQL errors are injected with triggers: a
    due scheduled task whose enqueue always fails (next to a healthy due task), and every
    heartbeat UPDATE failing for ~6 s (two lease TTLs). Each step needs its own savepoint:
    the scheduler lease must stay held (never expired) throughout, the healthy task must
    still be enqueued, and the heartbeat must resume once the errors stop.
    """
    env = _env(database, {"SCHEDULER_RECHECK_INTERVAL": 3})
    _migrate(database, env)
    admin = _connect(database)
    try:
        for table in ("sqlery_queued_job", "sqlery_scheduled_task", "sqlery_daemon_lease"):
            admin.execute(f"DELETE FROM {table}")
        admin.execute("DROP TABLE IF EXISTS idle110_fail CASCADE")
        admin.execute("DROP FUNCTION IF EXISTS idle110_poison_job() CASCADE")
        admin.execute("DROP FUNCTION IF EXISTS idle110_poison_worker() CASCADE")
        admin.execute(_POISON)
        with _Worker(env):
            time.sleep(3)
            _run(
                _ADD_TASK,
                env,
                IDLE110_DUE_IN="2",
                IDLE110_TASKS=json.dumps(
                    [
                        ["a-poison", "tests.test_idle_worker_commits_110.poison"],
                        ["b-good", "tests.test_idle_worker_commits_110.noop"],
                    ]
                ),
            )
            admin.execute("INSERT INTO idle110_fail VALUES (true)")
            start = time.monotonic()
            lapses, renewals = [], set()
            while time.monotonic() - start < 6:
                row = admin.execute(
                    "SELECT expires_at > now(), expires_at FROM sqlery_daemon_lease "
                    "WHERE queue_name = 'default'"
                ).fetchone()
                if row is None or not row[0]:
                    lapses.append(round(time.monotonic() - start, 1))
                else:
                    renewals.add(row[1])
                time.sleep(0.1)
            admin.execute("DELETE FROM idle110_fail")
            before = admin.execute("SELECT max(last_heartbeat) FROM sqlery_worker").fetchone()[0]
            time.sleep(3)
            after = admin.execute("SELECT max(last_heartbeat) FROM sqlery_worker").fetchone()[0]
            queued = [
                r[0] for r in admin.execute("SELECT task_path FROM sqlery_queued_job").fetchall()
            ]
    finally:
        admin.execute("DROP TABLE IF EXISTS idle110_fail CASCADE")
        admin.execute("DROP FUNCTION IF EXISTS idle110_poison_job() CASCADE")
        admin.execute("DROP FUNCTION IF EXISTS idle110_poison_worker() CASCADE")
        admin.close()
    assert not lapses, f"scheduler lease lapsed at t={lapses} while heartbeats were failing"
    assert len(renewals) >= 2, f"lease was renewed only {len(renewals)} times in 6 s"
    assert after > before, "heartbeat did not resume after the database errors stopped"
    assert queued == ["tests.test_idle_worker_commits_110.noop"], queued
