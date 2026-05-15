---
phase: 04-security-cleanup
plan: 03
subsystem: fastapi_sqlery
tags: [security, auth, fastapi, middleware, SEC-01]
requirements: [SEC-01]
dependency_graph:
  requires: [02-08]
  provides: [DashboardAuthMiddleware, install_auth, /healthz]
  affects: [src/sqlery/fastapi_sqlery/app.py, /trigger endpoint]
tech_stack:
  added: []
  patterns: [Starlette BaseHTTPMiddleware, hmac.compare_digest, env-override-wins, mount-auto-detect-via-root_path]
key_files:
  created:
    - src/sqlery/fastapi_sqlery/auth.py
    - tests/unit/test_dashboard_auth.py
    - .planning/phases/04-security-cleanup/04-03-SUMMARY.md
  modified:
    - src/sqlery/fastapi_sqlery/app.py
decisions:
  - "Use scope['root_path'] (not scope['app']) as primary mount-detection signal — Starlette rewrites scope['app'] to the inner app once routed, making the app-identity comparison unreliable after dispatch."
  - "/healthz bypass implemented inside middleware (not by route order) so it works regardless of middleware/router insertion order."
  - "Key file generated with umask 0o077 + explicit chmod 0o600; parent dir mkdir mode 0o700 with redundant chmod for already-existing dirs."
metrics:
  duration: "~25 min"
  completed: "2026-05-15"
  tasks: 2
  tests_added: 18
---

# Phase 04 Plan 03: Three-Mode Dashboard Auth Middleware (SEC-01) Summary

JWT-less HMAC-based dashboard auth with three runtime modes (standalone / disabled / inherit), auto-detected sub-app mounting, and persistent on-disk key with strict file permissions.

## What Was Built

- `src/sqlery/fastapi_sqlery/auth.py` — `DashboardAuthMiddleware` (Starlette `BaseHTTPMiddleware`), `resolve_auth_mode()`, `_load_or_create_key()`, and `install()` helper.
- Wired into `src/sqlery/fastapi_sqlery/app.py` via `install_auth(app)` BEFORE the `/trigger` router include, plus a new `/healthz` route.
- `tests/unit/test_dashboard_auth.py` — 18 tests covering every behavior bullet from the plan.

## Modes

| Mode | Trigger | Behavior |
|------|---------|----------|
| standalone (default) | top-level ASGI app, no env override | Require `X-Sqlery-Key` header; 401 `{"detail":"unauthorized"}` otherwise |
| disabled | `SQLERY_DASHBOARD_AUTH=disabled` | Pass-through + single WARNING log at install |
| inherit (explicit) | `SQLERY_DASHBOARD_AUTH=inherit` | Pass-through (parent app owns auth) |
| inherit (auto) | mounted under parent ASGI app (`scope['root_path']` non-empty) | Pass-through |

Explicit env value always beats mount auto-detection.

## Key Resolution

1. `SQLERY_DASHBOARD_API_KEY` env var (highest priority).
2. `./.sqlery/dashboard.key` file (read on startup).
3. Auto-generated 32-byte URL-safe token (`secrets.token_urlsafe(32)`); persisted with file mode `0o600`, parent dir `0o700`; key printed to stderr exactly once at startup.

## Security Properties

- `hmac.compare_digest` for constant-time comparison (verified by test with spy assertion).
- `request.headers.get("x-sqlery-key", "")` — case-insensitive lookup, never raw `scope["headers"]`.
- `/healthz` unconditional bypass for orchestrator/LB probes.
- Middleware installed BEFORE `include_router(_trigger_router)` so the `/trigger` admin endpoint (Plan 02-08) is protected.
- Key file written with umask `0o077` AND explicit `os.chmod(path, 0o600)` belt-and-suspenders.

## Test Coverage (18 tests)

- standalone: missing/wrong/correct key (3); case-insensitive header (1); compare_digest usage spy (1); `/healthz` bypass (1).
- disabled: pass-through (1); case-insensitive env value (1); WARNING logged at install (1).
- inherit: explicit env (1); auto-detected via real Starlette `Mount("/sub", sqlery_app)` (1).
- override precedence: explicit `standalone` wins over mount auto-detect (1).
- `resolve_auth_mode` unit: root_path fallback, default, explicit override (3).
- key file lifecycle: generate + persist + 0o600/0o700 perms + idempotency (1); env var wins (1).
- real-app smoke: `app.user_middleware` contains `DashboardAuth`, `/healthz` → 200, `/api/stats` no key → 401 (1).

## Deviations from Plan

**[Rule 1 - Bug] Mount auto-detection signal corrected**

- **Found during:** Task 1 test `test_inherit_auto_detected_when_mounted` failed.
- **Issue:** The plan's primary signal `request.scope['app'] is not our_app` does not fire after Starlette dispatches into the mounted inner app — at that point `scope['app']` IS our app. The fallback signal (`scope['app'] is None and scope['root_path']`) also doesn't fire because `scope['app']` is set.
- **Fix:** Promoted `scope['root_path']` non-empty to the PRIMARY mount-detection signal (Starlette always sets it on the inner request scope when mounted). `scope['app'] is not our_app` retained as secondary signal.
- **Files modified:** `src/sqlery/fastapi_sqlery/auth.py:resolve_auth_mode`
- **Commit:** c845380

**[Rule 3 - Blocker] Missing test-time deps**

- uvicorn + jinja2 not in `.venv` after initial `uv sync --extra dev`; installed both via `uv pip install` to enable the real-app smoke test. No `pyproject.toml` changes.

## Commits

- c845380 — Task 1: auth.py module + 17 unit tests
- c9966d9 — Task 2: wire into app.py before /trigger router

## Success Criteria

- [x] Unauthenticated requests to dashboard endpoints return 401 in standalone mode
- [x] `/healthz` is always reachable (bypass verified in test + manual smoke)
- [x] Mount-as-sub-app auto-detection passes Starlette real-mount test
- [x] Key persists in `./.sqlery/dashboard.key` with `0o600`, parent `0o700` (asserted via `stat.S_IMODE`)
- [x] Disabled mode logs single WARNING + passes through

## Self-Check: PASSED

- File `src/sqlery/fastapi_sqlery/auth.py` exists (157 lines).
- File `tests/unit/test_dashboard_auth.py` exists (267 lines, 18 tests, all green).
- Commit c845380 exists.
- Commit c9966d9 exists.
- `pyproject.toml` unchanged.
- `STATE.md` / `ROADMAP.md` not modified by this plan.
