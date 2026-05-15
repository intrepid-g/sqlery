"""Unit tests for dashboard auth middleware (SEC-01).

Covers all three modes (standalone / disabled / inherit), mount auto-detection, key file
lifecycle and permissions, ``/healthz`` bypass, and constant-time-compare evidence.
"""

from __future__ import annotations

import hmac
import logging
import os
import pathlib
import stat
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import Mount

from sqlery.fastapi_sqlery import auth as auth_mod
from sqlery.fastapi_sqlery.auth import (
    DashboardAuthMiddleware,
    _load_or_create_key,
    install,
    resolve_auth_mode,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app(*, install_auth: bool = True, key_path: pathlib.Path | None = None) -> FastAPI:
    """Build a minimal FastAPI app with one protected route + healthz, optionally with auth."""
    app = FastAPI()
    if install_auth:
        install(app, key_path=key_path)

    @app.get("/protected")
    def protected():
        return {"ok": True}

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    """Isolate every test from the user's env and CWD."""
    monkeypatch.delenv("SQLERY_DASHBOARD_AUTH", raising=False)
    monkeypatch.delenv("SQLERY_DASHBOARD_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    yield


# ---------------------------------------------------------------------------
# Standalone mode (default)
# ---------------------------------------------------------------------------

def test_standalone_no_header_returns_401(monkeypatch):
    monkeypatch.setenv("SQLERY_DASHBOARD_API_KEY", "secret-key")
    app = _make_app()
    client = TestClient(app)
    r = client.get("/protected")
    assert r.status_code == 401
    assert r.json() == {"detail": "unauthorized"}


def test_standalone_wrong_key_returns_401(monkeypatch):
    monkeypatch.setenv("SQLERY_DASHBOARD_API_KEY", "secret-key")
    app = _make_app()
    client = TestClient(app)
    r = client.get("/protected", headers={"X-Sqlery-Key": "wrong"})
    assert r.status_code == 401


def test_standalone_correct_key_returns_200(monkeypatch):
    monkeypatch.setenv("SQLERY_DASHBOARD_API_KEY", "secret-key")
    app = _make_app()
    client = TestClient(app)
    r = client.get("/protected", headers={"X-Sqlery-Key": "secret-key"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_standalone_header_case_insensitive(monkeypatch):
    monkeypatch.setenv("SQLERY_DASHBOARD_API_KEY", "secret-key")
    app = _make_app()
    client = TestClient(app)
    # Lowercase header name still recognized (Starlette normalizes).
    r = client.get("/protected", headers={"x-sqlery-key": "secret-key"})
    assert r.status_code == 200


def test_healthz_bypasses_auth_in_standalone(monkeypatch):
    monkeypatch.setenv("SQLERY_DASHBOARD_API_KEY", "secret-key")
    app = _make_app()
    client = TestClient(app)
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_standalone_uses_compare_digest(monkeypatch):
    """Constant-time-compare evidence: hmac.compare_digest must be called on key check."""
    monkeypatch.setenv("SQLERY_DASHBOARD_API_KEY", "secret-key")
    app = _make_app()
    client = TestClient(app)
    with patch.object(auth_mod.hmac, "compare_digest", wraps=hmac.compare_digest) as spy:
        r = client.get("/protected", headers={"X-Sqlery-Key": "secret-key"})
        assert r.status_code == 200
        assert spy.called, "hmac.compare_digest must be used for key comparison"


# ---------------------------------------------------------------------------
# Disabled mode
# ---------------------------------------------------------------------------

def test_disabled_mode_passthrough(monkeypatch):
    monkeypatch.setenv("SQLERY_DASHBOARD_AUTH", "disabled")
    app = _make_app()
    client = TestClient(app)
    r = client.get("/protected")
    assert r.status_code == 200


def test_disabled_mode_case_insensitive(monkeypatch):
    monkeypatch.setenv("SQLERY_DASHBOARD_AUTH", "DISABLED")
    app = _make_app()
    client = TestClient(app)
    r = client.get("/protected")
    assert r.status_code == 200


def test_disabled_logs_warning_at_install(monkeypatch, caplog):
    monkeypatch.setenv("SQLERY_DASHBOARD_AUTH", "disabled")
    with caplog.at_level(logging.WARNING, logger="sqlery.fastapi_sqlery.auth"):
        _make_app()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "DISABLED" in warnings[0].getMessage()


# ---------------------------------------------------------------------------
# Inherit mode (explicit)
# ---------------------------------------------------------------------------

def test_inherit_explicit_short_circuits(monkeypatch):
    monkeypatch.setenv("SQLERY_DASHBOARD_AUTH", "inherit")
    app = _make_app()
    client = TestClient(app)
    r = client.get("/protected")
    # No key provided, but inherit mode → pass through.
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Inherit mode (auto-detected via real mount)
# ---------------------------------------------------------------------------

def test_inherit_auto_detected_when_mounted(monkeypatch):
    """When mounted under a parent Starlette app, middleware short-circuits."""
    monkeypatch.setenv("SQLERY_DASHBOARD_API_KEY", "secret-key")
    sqlery_app = _make_app()
    parent = Starlette(routes=[Mount("/sub", app=sqlery_app)])
    client = TestClient(parent)
    # Request goes through parent → scope['app'] inside middleware is parent, not sqlery_app.
    r = client.get("/sub/protected")
    assert r.status_code == 200, f"mounted app should inherit auth, got {r.status_code}"


def test_explicit_standalone_overrides_mount_autodetect(monkeypatch):
    """Explicit SQLERY_DASHBOARD_AUTH=standalone wins even when mounted."""
    monkeypatch.setenv("SQLERY_DASHBOARD_API_KEY", "secret-key")
    monkeypatch.setenv("SQLERY_DASHBOARD_AUTH", "standalone")
    sqlery_app = _make_app()
    parent = Starlette(routes=[Mount("/sub", app=sqlery_app)])
    client = TestClient(parent)
    r = client.get("/sub/protected")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# resolve_auth_mode unit (root_path fallback)
# ---------------------------------------------------------------------------

class _FakeRequest:
    def __init__(self, scope):
        self.scope = scope


def test_resolve_mode_root_path_fallback(monkeypatch):
    """When scope['app'] is None but root_path is set → inherit."""
    monkeypatch.delenv("SQLERY_DASHBOARD_AUTH", raising=False)
    our_app = object()
    req = _FakeRequest({"app": None, "root_path": "/sub"})
    assert resolve_auth_mode(our_app, req) == "inherit"


def test_resolve_mode_default_standalone(monkeypatch):
    monkeypatch.delenv("SQLERY_DASHBOARD_AUTH", raising=False)
    our_app = object()
    req = _FakeRequest({"app": our_app, "root_path": ""})
    assert resolve_auth_mode(our_app, req) == "standalone"


def test_resolve_mode_explicit_overrides_autodetect(monkeypatch):
    monkeypatch.setenv("SQLERY_DASHBOARD_AUTH", "standalone")
    our_app = object()
    other_app = object()
    req = _FakeRequest({"app": other_app, "root_path": "/sub"})
    assert resolve_auth_mode(our_app, req) == "standalone"


# ---------------------------------------------------------------------------
# Key file lifecycle + permissions
# ---------------------------------------------------------------------------

def test_load_or_create_key_generates_and_persists(tmp_path):
    key_path = tmp_path / ".sqlery" / "dashboard.key"
    key1 = _load_or_create_key(key_path)
    assert key1 and len(key1) >= 32
    assert key_path.exists()
    # File mode 0o600
    file_mode = stat.S_IMODE(os.stat(key_path).st_mode)
    assert file_mode == 0o600, f"expected 0o600, got {oct(file_mode)}"
    # Dir mode 0o700
    dir_mode = stat.S_IMODE(os.stat(key_path.parent).st_mode)
    assert dir_mode == 0o700, f"expected 0o700, got {oct(dir_mode)}"
    # Idempotent: subsequent call reads same key.
    key2 = _load_or_create_key(key_path)
    assert key2 == key1


def test_load_or_create_key_env_var_wins(tmp_path, monkeypatch):
    key_path = tmp_path / ".sqlery" / "dashboard.key"
    monkeypatch.setenv("SQLERY_DASHBOARD_API_KEY", "env-key")
    key = _load_or_create_key(key_path)
    assert key == "env-key"
    # No file written when env var is set.
    assert not key_path.exists()


# ---------------------------------------------------------------------------
# Real app smoke test (Plan 02-08 /trigger coverage)
# ---------------------------------------------------------------------------

def test_real_app_protects_routes_and_allows_healthz(monkeypatch):
    """End-to-end against the real FastAPI app: 401 on /api/jobs, 200 on /healthz."""
    monkeypatch.setenv("SQLERY_DASHBOARD_API_KEY", "test-key")
    # Skip cleanly if uvicorn isn't installed in this test env (app.py imports it).
    pytest.importorskip("uvicorn")
    from sqlery.fastapi_sqlery import app as app_module

    client = TestClient(app_module.app)
    r = client.get("/healthz")
    assert r.status_code == 200

    # /api/stats without key → 401 (only valid when this test runs first; if app was
    # imported before env var was set, expected_key may differ — so we just assert that
    # an unauthenticated request is rejected with 401).
    r = client.get("/api/stats")
    assert r.status_code == 401
