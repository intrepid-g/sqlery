"""Regression tests for #12: standalone entry points must pick up SQLERY_DATABASE_URL.

``sqlery daemon``, the jobs/tasks/queues/cleanup CLIs and the dashboard (sqlery-web)
never call initialize(); before the fix only ``sqlery init`` / ``sqlery worker`` built
the engine, so every other entry point failed with "Database not initialized".
"""

import pytest


@pytest.fixture
def env_url(tmp_path, monkeypatch):
    """Standalone mode with SQLERY_DATABASE_URL set and the engine not initialized."""
    monkeypatch.setenv("SQLERY_FORCE_STANDALONE", "1")
    monkeypatch.setenv("SQLERY_DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    from sqlery.fastapi_sqlery import database as db_mod

    monkeypatch.setattr(db_mod, "_engine", None)


def test_get_engine_initialises_from_env(env_url):
    from sqlery.fastapi_sqlery.database import get_engine

    engine = get_engine()
    assert engine is not None
    assert str(engine.url).startswith("sqlite")


def test_cli_jobs_list_works_without_initialize(env_url):
    from typer.testing import CliRunner

    from sqlery.core.cli import jobs_app

    result = CliRunner().invoke(jobs_app, ["list"])
    assert result.exit_code == 0, result.output
    assert "Database not initialized" not in result.output


def test_dashboard_page_works_without_initialize(env_url):
    from fastapi.testclient import TestClient

    from sqlery.fastapi_sqlery.app import app
    from sqlery.fastapi_sqlery.auth import DashboardAuthMiddleware

    key = ""
    for mw in app.user_middleware:
        if mw.cls is DashboardAuthMiddleware:
            key = mw.kwargs.get("expected_key") or ""
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/", headers={"X-Sqlery-Key": key})
    assert response.status_code == 200, response.text[:500]
