"""Regression: standalone FastAPI dashboard HTML pages render (#73)."""

import pytest


@pytest.fixture
def backend(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLERY_FORCE_STANDALONE", "1")
    monkeypatch.setenv("SQLERY_DASHBOARD_API_KEY", "k")
    from sqlery.compat import get_backend, initialize

    initialize(database_url=f"sqlite:///{tmp_path}/t.db")
    return get_backend()


@pytest.mark.parametrize("path", ["/", "/jobs", "/workers", "/scheduled-tasks", "/registries"])
def test_dashboard_pages_render(backend, path):
    from fastapi.testclient import TestClient

    from sqlery.fastapi_sqlery.app import app

    for _ in range(60):  # more than one page of jobs
        backend.create_job(
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
    backend.update_worker_heartbeat(worker_id="w1", status="idle")
    response = TestClient(app, raise_server_exceptions=False).get(
        path, headers={"X-Sqlery-Key": "k"}
    )
    assert response.status_code == 200, response.text[:500]
