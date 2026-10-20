"""Regression: standalone FastAPI dashboard HTML pages render (#73)."""

import pytest


@pytest.fixture
def backend(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLERY_FORCE_STANDALONE", "1")
    from sqlery.compat import get_backend, initialize

    initialize(database_url=f"sqlite:///{tmp_path}/t.db")
    return get_backend()


def _create_job(backend):
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


def _client_and_headers():
    """Client plus auth headers for whatever key the (import-once) app middleware expects."""
    from fastapi.testclient import TestClient

    from sqlery.fastapi_sqlery.app import app
    from sqlery.fastapi_sqlery.auth import DashboardAuthMiddleware

    key = ""
    for mw in app.user_middleware:
        if mw.cls is DashboardAuthMiddleware:
            key = mw.kwargs.get("expected_key") or ""
    return TestClient(app, raise_server_exceptions=False), {"X-Sqlery-Key": key}


@pytest.mark.parametrize("path", ["/", "/jobs", "/workers", "/scheduled-tasks", "/registries"])
def test_dashboard_pages_render(backend, path):
    client, headers = _client_and_headers()
    for _ in range(60):  # more than one page of jobs
        _create_job(backend)
    backend.update_worker_heartbeat(worker_id="w1", status="idle")
    response = client.get(path, headers=headers)
    assert response.status_code == 200, response.text[:500]


def test_job_detail_and_not_found_render(backend):
    client, headers = _client_and_headers()
    job = _create_job(backend)
    job_id = getattr(job, "id", job)
    assert client.get(f"/jobs/{job_id}", headers=headers).status_code == 200
    missing = client.get("/jobs/999999999", headers=headers)
    assert missing.status_code == 404
    assert "not found" in missing.text
