"""Concurrent first start on an empty PostgreSQL database must not collide (issue #10)."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid

import pytest

PG_URL = os.environ.get("SQLERY_TEST_PG_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not PG_URL, reason="SQLERY_TEST_PG_URL not set; PG required"),
]

PROCESSES = 4
TRIALS = 5
_START = (
    "from sqlery.fastapi_sqlery.database import init_database; init_database({url!r})"
)


def _admin_url_and_db(base: str, name: str) -> tuple[str, str]:
    head, _, _ = base.rpartition("/")
    return base, f"{head}/{name}"


@pytest.fixture
def fresh_db():
    import psycopg

    base = PG_URL.replace("postgresql+psycopg://", "postgresql://")
    name = f"sqlery_race_{uuid.uuid4().hex[:10]}"
    admin, url = _admin_url_and_db(base, name)
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    yield url
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.mark.parametrize("trial", range(TRIALS))
def test_parallel_first_start_on_empty_database_all_succeed(fresh_db, trial):
    code = _START.format(url=fresh_db)
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        for _ in range(PROCESSES)
    ]
    results = [(p.wait(timeout=120), p.stderr.read()) for p in procs]
    failures = [err[-400:] for rc, err in results if rc != 0]
    assert not failures, f"{len(failures)}/{PROCESSES} starts failed: {failures}"
