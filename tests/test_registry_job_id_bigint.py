"""sqlery_registry.job_id must hold 64-bit (snowflake-style) job ids, like #77 (#108)."""

import os
import uuid
from pathlib import Path

import pytest

from tests.pg_url import sqlalchemy_pg_url

needs_pg = pytest.mark.skipif(
    "SQLERY_TEST_PG_URL" not in os.environ, reason="needs PostgreSQL: SQLERY_TEST_PG_URL"
)
REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _fresh_backend():
    """Don't inherit (or leak) a backend singleton cached by another test."""
    from sqlery.compat import _reset_backend

    _reset_backend()
    yield
    _reset_backend()


def _make_job(backend):
    return backend.create_job(
        task_path="x.y",
        kwargs={},
        queue_name="default",
        priority=0,
        scheduled_at=None,
        max_retries=0,
        retry_backoff=1.0,
        allow_parallel=True,
        timeout_seconds=None,
    )


def test_registry_job_id_column_is_bigint():
    from sqlalchemy import BigInteger

    from sqlery.core.models import JobRegistry

    assert isinstance(JobRegistry.__table__.c.job_id.type, BigInteger)


def test_raw_ddl_declares_registry_job_id_bigint():
    from sqlery import schema

    for ddl in (schema.REGISTRY_TABLE_SQLITE, schema.REGISTRY_TABLE_POSTGRESQL):
        assert "job_id BIGINT NOT NULL" in ddl


@pytest.mark.postgres
@needs_pg
def test_add_job_to_registry_accepts_a_standalone_job_id(monkeypatch):
    monkeypatch.setenv("SQLERY_FORCE_STANDALONE", "1")
    from sqlery.compat import get_backend, initialize

    initialize(database_url=sqlalchemy_pg_url(os.environ["SQLERY_TEST_PG_URL"]))
    backend = get_backend()
    job = _make_job(backend)
    assert job.id > 2**31 - 1  # standalone ids are ~4.6e17
    backend.add_job_to_registry(job.id, "started")


@pytest.mark.postgres
@needs_pg
def test_alembic_upgrade_widens_an_integer_registry_job_id(monkeypatch):
    """An INTEGER sqlery_registry.job_id database is widened by `alembic upgrade head`."""
    pytest.importorskip("alembic.config")
    import sqlalchemy as sa
    from alembic.config import Config

    from alembic import command

    admin_url = sqlalchemy_pg_url(os.environ["SQLERY_TEST_PG_URL"])
    name = f"sqlery_reg108_{uuid.uuid4().hex[:8]}"
    admin = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    url = sa.make_url(admin_url).set(database=name).render_as_string(hide_password=False)
    engine = sa.create_engine(url)
    try:
        monkeypatch.setenv("SQLERY_DATABASE_URL", url)
        cfg = Config(str(REPO / "alembic.ini"))
        cfg.set_main_option("script_location", str(REPO / "alembic"))

        def job_id_type():
            with engine.connect() as conn:
                return conn.execute(
                    sa.text(
                        "SELECT data_type FROM information_schema.columns "
                        "WHERE table_name='sqlery_registry' AND column_name='job_id'"
                    )
                ).scalar()

        # The pre-fix state: tables as an older release created them (registry.job_id
        # INTEGER), stamped at the previous head. (The migration chain does not replay
        # from an empty database: 0001 already creates every table.)
        monkeypatch.setenv("SQLERY_FORCE_STANDALONE", "1")
        from sqlery.compat import initialize

        initialize(database_url=url)  # creates the schema
        with engine.begin() as conn:
            conn.execute(sa.text("ALTER TABLE sqlery_registry ALTER COLUMN job_id TYPE INTEGER"))
        command.stamp(cfg, "20260620_0017")
        assert job_id_type() == "integer"

        command.upgrade(cfg, "head")
        assert job_id_type() == "bigint"

        from sqlery.compat import get_backend

        backend = get_backend()
        job = _make_job(backend)
        assert job.id > 2**31 - 1
        backend.add_job_to_registry(job.id, "started")
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
