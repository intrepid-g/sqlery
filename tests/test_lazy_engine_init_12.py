"""Standalone entry points must open the database named by SQLERY_DATABASE_URL (#12)."""

import pytest
from typer.testing import CliRunner

from sqlery.fastapi_sqlery import database


def _standalone_env(monkeypatch):
    """Force standalone mode with no engine and fresh config/backend singletons."""
    from sqlery import compat

    monkeypatch.setenv("SQLERY_FORCE_STANDALONE", "1")
    monkeypatch.setattr(database, "_engine", None)
    compat._reset_backend()


@pytest.fixture
def env_only_db(tmp_path, monkeypatch):
    """Only SQLERY_DATABASE_URL is set; no initialize() call, no engine."""
    from sqlery import compat

    _standalone_env(monkeypatch)
    monkeypatch.setenv("SQLERY_DATABASE_URL", f"sqlite:///{tmp_path / 'x.db'}")
    monkeypatch.chdir(tmp_path)
    yield
    compat._reset_backend()


def test_get_engine_builds_engine_from_env_url(env_only_db):
    engine = database.get_engine()
    assert str(engine.url).startswith("sqlite:///")
    assert database.get_engine() is engine  # built once, then reused


def test_get_engine_without_any_url_still_raises(monkeypatch):
    from sqlery import compat

    _standalone_env(monkeypatch)
    monkeypatch.delenv("SQLERY_DATABASE_URL", raising=False)
    compat._reset_backend()
    with pytest.raises(RuntimeError, match="Database not initialized"):
        database.get_engine()


def test_jobs_list_cli_works_with_env_url_only(env_only_db):
    from sqlery.core.cli import jobs_app

    result = CliRunner().invoke(jobs_app, ["list"])
    assert "Database not initialized" not in result.output
    assert result.exit_code == 0, result.output


def test_dashboard_page_serves_with_env_url_only(env_only_db):
    from fastapi.testclient import TestClient

    from sqlery.fastapi_sqlery.app import app

    with TestClient(app, raise_server_exceptions=False) as client:
        key = _dashboard_key(app)
        resp = client.get("/api/jobs", headers={"X-Sqlery-Key": key})
    assert resp.status_code == 200, resp.text


def _dashboard_key(app) -> str:
    """The key the app's auth middleware (installed at import) expects."""
    mw = next(m for m in app.user_middleware if "expected_key" in m.kwargs)
    return mw.kwargs["expected_key"]
