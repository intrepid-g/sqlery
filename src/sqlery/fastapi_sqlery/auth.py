"""Dashboard authentication middleware for the FastAPI standalone app (SEC-01).

Three modes:
- standalone (default): require ``X-Sqlery-Key`` header; reject otherwise with 401.
- disabled: opt-out via ``SQLERY_DASHBOARD_AUTH=disabled``; pass-through; one WARNING at install.
- inherit: trust parent app's auth. Explicit via ``SQLERY_DASHBOARD_AUTH=inherit`` or auto-detected
  when the app is mounted under another ASGI app (Starlette ``scope['app']`` differs).

Explicit env value always wins over auto-detection.

``/healthz`` ALWAYS bypasses authentication so liveness probes don't depend on credentials.
"""

from __future__ import annotations

import hmac
import logging
import os
import pathlib
import secrets
import sys
from typing import Literal

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

_AUTH_ENV_VAR = "SQLERY_DASHBOARD_AUTH"
_API_KEY_ENV_VAR = "SQLERY_DASHBOARD_API_KEY"
_DEFAULT_KEY_PATH = pathlib.Path("./.sqlery/dashboard.key")
_HEALTH_PATH = "/healthz"

AuthMode = Literal["standalone", "disabled", "inherit"]


def resolve_auth_mode(our_app, request) -> AuthMode:
    """Resolve which auth mode is active for this request.

    Order of precedence:
    1. Explicit ``SQLERY_DASHBOARD_AUTH`` env var (case-insensitive). Valid values:
       ``standalone``, ``disabled``, ``inherit``.
    2. Auto-detect: if ``request.scope['app']`` exists and is not our app, we're mounted
       under a parent ASGI app → ``inherit``.
    3. Fallback: if ``scope['app']`` is None but ``scope['root_path']`` is non-empty,
       also assume mounted → ``inherit``.
    4. Default: ``standalone``.
    """
    explicit = os.environ.get(_AUTH_ENV_VAR, "").strip().lower()
    if explicit in ("standalone", "disabled", "inherit"):
        return explicit  # type: ignore[return-value]

    # When mounted under a parent ASGI app, Starlette populates `root_path` on the inner
    # request scope with the mount prefix (e.g. "/sub"). This is the most reliable mount
    # signal — `scope['app']` is rewritten to the inner app once routed, so comparing it
    # to ``our_app`` does not work after the route dispatch.
    if request.scope.get("root_path"):
        return "inherit"
    scope_app = request.scope.get("app")
    if scope_app is not None and scope_app is not our_app:
        return "inherit"
    return "standalone"


def _load_or_create_key(key_path: pathlib.Path | None = None) -> str:
    """Resolve the dashboard API key.

    Order:
    1. ``SQLERY_DASHBOARD_API_KEY`` env var.
    2. Existing key file at ``key_path`` (default: ``./.sqlery/dashboard.key``).
    3. Generate a new 32-byte URL-safe token, persist with strict permissions, log once.
    """
    env_key = os.environ.get(_API_KEY_ENV_VAR)
    if env_key:
        return env_key

    path = key_path if key_path is not None else _DEFAULT_KEY_PATH
    if path.exists():
        return path.read_text().strip()

    # Generate a new key; ensure directory + file permissions are restrictive.
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Tighten dir perms even if parent already existed.
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass

    key = secrets.token_urlsafe(32)
    old_umask = os.umask(0o077)
    try:
        path.write_text(key)
    finally:
        os.umask(old_umask)
    os.chmod(path, 0o600)

    # Log exactly once to stderr at INFO level (operator visibility on first run).
    print(
        f"Generated dashboard API key (stored at {path}): {key}",
        file=sys.stderr,
    )
    logger.info("Generated dashboard API key (stored at %s)", path)
    return key


class DashboardAuthMiddleware(BaseHTTPMiddleware):
    """Starlette/FastAPI middleware enforcing the three-mode dashboard auth policy."""

    def __init__(self, app, our_app, expected_key: str | None):
        super().__init__(app)
        self._our_app = our_app
        self._expected_key = expected_key or ""

    async def dispatch(self, request, call_next):
        # Health check always bypasses auth in every mode.
        if request.url.path == _HEALTH_PATH:
            return await call_next(request)

        mode = resolve_auth_mode(self._our_app, request)
        if mode in ("disabled", "inherit"):
            return await call_next(request)

        # Standalone: require X-Sqlery-Key.
        provided = request.headers.get("x-sqlery-key", "")
        if not hmac.compare_digest(provided.encode("utf-8"), self._expected_key.encode("utf-8")):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)


def install(app, *, key_path: pathlib.Path | None = None) -> None:
    """Install ``DashboardAuthMiddleware`` on ``app``.

    Resolves the install-time mode by inspecting the env var only (mount detection is
    per-request). If ``disabled``, logs a single WARNING and skips key loading. Otherwise
    loads / creates the key and adds the middleware.

    MUST be called BEFORE ``app.include_router(...)`` so the middleware covers every route
    (including the ``/trigger`` admin endpoint from Plan 02-08).
    """
    explicit = os.environ.get(_AUTH_ENV_VAR, "").strip().lower()
    if explicit == "disabled":
        logger.warning(
            "Sqlery dashboard auth is DISABLED (SQLERY_DASHBOARD_AUTH=disabled). "
            "Admin endpoints are unauthenticated."
        )
        app.add_middleware(DashboardAuthMiddleware, our_app=app, expected_key="")
        return

    key: str | None = None
    if explicit != "inherit":
        # standalone (default) → need a key. inherit also fine without one (mw won't check).
        key = _load_or_create_key(key_path)

    app.add_middleware(DashboardAuthMiddleware, our_app=app, expected_key=key)
