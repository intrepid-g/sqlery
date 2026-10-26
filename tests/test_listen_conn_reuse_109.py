"""#109: a NOTIFY worker keeps ONE LISTEN connection for the life of the worker.

With ``SQLERY_PG_NOTIFY`` on, ``WorkerProcess`` used to close its LISTEN connection before
every per-job fork and open a new one (connect + one ``LISTEN`` per queue) afterwards, so
every executed job cost the database an extra connection. End-to-end contract: real
PostgreSQL scratch database, real ``migrate``, 5 real jobs run by
``WorkerProcess(...).run(max_jobs=5)``; the LISTEN connection is opened at most twice (once
at start, tolerating one more) and the worker makes at most 2 psycopg connections of its own.

Runs in a subprocess so Django settings do not clash with the rest of the test session.
Needs SQLERY_TEST_PG_URL (a role that may CREATE DATABASE).
"""

import json
import os
import subprocess
import sys
import textwrap
import uuid
from urllib.parse import urlparse

import pytest

pytestmark = pytest.mark.postgres

PG_URL = os.environ.get("SQLERY_TEST_PG_URL", "")
JOBS = 5

_SCRIPT = textwrap.dedent("""
    import json, os, sys
    import django
    from django.conf import settings

    cfg = json.loads(sys.argv[1])
    settings.configure(
        DATABASES={"default": {
            "ENGINE": "django.db.backends.postgresql", "NAME": cfg["name"],
            "USER": cfg["user"], "PASSWORD": cfg["password"], "HOST": cfg["host"],
            "PORT": cfg["port"],
        }},
        INSTALLED_APPS=["sqlery.django_sqlery"], USE_TZ=True,
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField", SECRET_KEY="t109",
        DJANGO_SQL_JOBS={
            "TRIGGER_MODE": "daemon", "ENABLE_DAEMON": False, "WORKER_QUEUES": ["default"],
            "WORKER_POLL_INTERVAL": 1, "SQLERY_PG_NOTIFY": True,
        },
    )
    django.setup()
    from django.core.management import call_command

    call_command("migrate", verbosity=0)

    from sqlery.decorators import job

    @job
    def noop():
        return "ok"

    for _ in range(cfg["jobs"]):
        noop.delay()

    from sqlery.core import worker as worker_mod
    from sqlery.core.worker import WorkerProcess

    opens, connects = [], []
    original_open = WorkerProcess._open_listen_conn

    def spy_open(self):
        opens.append(1)
        return original_open(self)

    WorkerProcess._open_listen_conn = spy_open
    original_connect = worker_mod._psycopg.connect

    def spy_connect(*a, **kw):
        if kw.get("autocommit"):  # only the worker's LISTEN connection, not Django's
            connects.append(1)
        return original_connect(*a, **kw)

    worker_mod._psycopg.connect = spy_connect

    WorkerProcess(queues=["default"]).run(max_jobs=cfg["jobs"])

    from sqlery.django_sqlery.models import JobAll
    done = JobAll.objects.filter(status="success").count()
    print("RESULT" + json.dumps({"opens": len(opens), "connects": len(connects), "done": done}))
    """)


@pytest.fixture
def scratch_db():
    import psycopg

    base = urlparse(PG_URL)
    name = f"sqlery_t109_{uuid.uuid4().hex[:8]}"
    admin = psycopg.connect(PG_URL, autocommit=True)
    admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield {
            "name": name,
            "user": base.username or "",
            "password": base.password or "",
            "host": base.hostname or "",
            "port": base.port or 5432,
        }
    finally:
        admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.close()


@pytest.mark.skipif(not PG_URL, reason="SQLERY_TEST_PG_URL not set")
def test_notify_worker_keeps_one_listen_connection_across_jobs(scratch_db):
    """#109: LISTEN connection is not reopened per job (was 6 opens for 5 jobs)."""
    cfg = dict(scratch_db, jobs=JOBS)
    proc = subprocess.run(
        [sys.executable, "-c", _SCRIPT, json.dumps(cfg)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT")]
    assert (
        lines
    ), f"worker script failed:\nstdout={proc.stdout[-2000:]}\nstderr={proc.stderr[-2000:]}"
    result = json.loads(lines[-1][len("RESULT") :])
    assert result["done"] == JOBS, result
    assert (
        result["opens"] <= 2
    ), f"LISTEN connection opened {result['opens']} times for {JOBS} jobs (1 at start + 1 per job)"
    assert (
        result["connects"] <= 2
    ), f"worker opened {result['connects']} LISTEN psycopg connections for {JOBS} jobs"
