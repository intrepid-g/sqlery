"""Issue #107: migration 0028 must be re-runnable after an interrupted index build.

``AddIndexConcurrently`` leaves an INVALID ``sqlery_job_pending_idx`` behind when the
build is cancelled (deadlock victim, killed container); the migration is not recorded
and every later ``migrate`` failed with ``relation "sqlery_job_pending_idx" already
exists``. Migrations 0030/0031 use plain (transactional) DDL and Alembic has no
CONCURRENTLY, so 0028 is the only affected migration.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg import sql

ADMIN_URL = os.environ.get("SQLERY_TEST_PG_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not ADMIN_URL, reason="needs SQLERY_TEST_PG_URL / DATABASE_URL"),
]

SETTINGS = textwrap.dedent("""
    import os
    from urllib.parse import urlparse

    _u = urlparse(os.environ["DATABASE_URL"])
    DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql",
                             "NAME": _u.path.lstrip("/"), "USER": _u.username or "",
                             "PASSWORD": _u.password or "", "HOST": _u.hostname,
                             "PORT": _u.port or 5432}}
    INSTALLED_APPS = ["sqlery.django_sqlery"]
    SECRET_KEY = "repro"
    USE_TZ = True
    DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
    """)


@pytest.fixture
def db_url(tmp_path):
    (tmp_path / "settings_107.py").write_text(SETTINGS)
    admin = psycopg.connect(ADMIN_URL, autocommit=True)
    name = f"sqlery_t107_{uuid.uuid4().hex[:8]}"
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = urlunsplit(urlsplit(ADMIN_URL)._replace(path=f"/{name}"))
    yield url, tmp_path
    admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
    admin.close()


def _migrate(url, settings_dir, *args):
    repo = str(Path(__file__).resolve().parent.parent)
    env = {
        **os.environ,
        "DATABASE_URL": url,
        "DJANGO_SETTINGS_MODULE": "settings_107",
        "PYTHONPATH": os.pathsep.join([str(settings_dir), repo, os.environ.get("PYTHONPATH", "")]),
    }
    return subprocess.Popen(
        [sys.executable, "-m", "django", "migrate", "sqlery", *args, "--noinput"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def test_migrate_succeeds_again_after_cancelled_concurrent_index_build(db_url):
    url, settings_dir = db_url
    assert _migrate(url, settings_dir, "0027").wait(timeout=300) == 0
    # An open write lock on the table makes the CONCURRENTLY build wait; cancel it.
    blocker = psycopg.connect(url)
    watcher = psycopg.connect(url, autocommit=True)
    try:
        blocker.execute("LOCK TABLE sqlery_queued_job IN ROW EXCLUSIVE MODE")
        proc = _migrate(url, settings_dir)
        deadline = time.time() + 120
        while time.time() < deadline:
            rows = watcher.execute(
                "SELECT pid FROM pg_stat_activity WHERE query ILIKE 'CREATE INDEX CONCURRENTLY%' "
                "AND state = 'active'"
            ).fetchall()
            if rows:
                break
            time.sleep(0.2)
        else:
            proc.kill()
            pytest.fail("the concurrent index build never started")
        watcher.execute("SELECT pg_cancel_backend(%s)", (rows[0][0],))
        assert proc.wait(timeout=120) != 0  # the interrupted start
        blocker.rollback()
        assert (
            watcher.execute("SELECT count(*) FROM pg_index WHERE NOT indisvalid").fetchone()[0] == 1
        )

        rerun = _migrate(url, settings_dir)
        out = rerun.communicate(timeout=300)[0]
        assert rerun.returncode == 0, out[-600:]
        assert (
            watcher.execute("SELECT count(*) FROM pg_index WHERE NOT indisvalid").fetchone()[0] == 0
        )
        valid = watcher.execute(
            "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
            "WHERE c.relname = 'sqlery_job_pending_idx'"
        ).fetchall()
        assert valid == [(True,)]
        # a further migrate is a no-op
        assert _migrate(url, settings_dir).wait(timeout=300) == 0
    finally:
        blocker.close()
        watcher.close()
