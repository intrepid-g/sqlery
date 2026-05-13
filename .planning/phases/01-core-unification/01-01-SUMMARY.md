---
phase: 01-core-unification
plan: 01
subsystem: core/db-resilience
tags: [core, django-decoupling, fork-safety]
requires:
  - sqlery.compat.get_config
  - sqlery.compat.is_django_mode
  - sqlery.compat.get_backend
provides:
  - "core/db_resilience.py: Django-free import path; cross-backend retry exception tuple"
  - "core/model_utils.py: explicit RuntimeError when Django unavailable"
  - "core/daemon_runner.py + core/worker_runner.py: Django bootstrap is now a guarded no-op in standalone mode"
affects:
  - All callers of retry_on_db_error (worker, daemon, claiming algorithm)
  - All callers of configure_connection_resilience (worker/daemon startup)
tech-stack:
  added: []
  patterns: [guarded-import, retryable-exception-tuple, function-local-circular-import-avoidance]
key-files:
  modified:
    - src/sqlery/core/db_resilience.py
    - src/sqlery/core/model_utils.py
    - src/sqlery/core/daemon_runner.py
    - src/sqlery/core/worker_runner.py
  created: []
decisions:
  - "Use a module-level _RETRYABLE_EXC tuple built from whichever of django.db / sqlalchemy.exc is installed, falling back to (Exception,) with a warning so pure-import tests still pass."
  - "Function-local import of sqlery.compat in db_resilience helpers to avoid circular imports at module load."
  - "configure_connection_resilience() no-ops in standalone mode when no cursor handle is available; vendor-aware plumbing through get_backend().vendor is in place for when SQLAlchemyBackend exposes it."
metrics:
  duration: ~15m
  completed: 2026-05-13
requirements: [UNIF-03, UNIF-04, UNIF-05, UNIF-06]
---

# Phase 1 Plan 01: Guard Django Imports in core/ Summary

One-liner: Replaced unguarded `django.db` imports in `core/db_resilience.py` with cross-backend guarded imports (Django + SQLAlchemy) and tightened the remaining `core/` runner / model-utility Django guards so the whole `sqlery.core.*` surface imports cleanly without Django installed.

## What Was Built

- **`src/sqlery/core/db_resilience.py` (full rewrite of imports + helpers):**
  - Guarded `try/except ImportError` blocks for `django.db` (`OperationalError`, `DatabaseError`, `connection`, `connections`) and for `sqlalchemy.exc` (`OperationalError`, `DBAPIError`).
  - Module-level `_RETRYABLE_EXC` tuple built from whichever exception classes are actually importable, with a `(Exception,)` fallback plus a load-time warning.
  - New `_reset_connections()` helper: calls `django.db.connections.close_all()` when Django is the active backend, otherwise attempts `get_backend().reset_connections()` (function-local import to avoid circulars) and logs-and-continues if absent.
  - New `_get_setting(name, default)` helper that routes through `sqlery.compat.get_config`, replacing the old `from sqlery.django_sqlery.settings import get_setting` shim.
  - New `_resolve_active_connection_and_vendor()` helper: returns the live Django `connection` + `vendor` in Django mode, or `(None, backend.vendor)` in standalone mode, or `(None, None)` when neither is available.
  - `configure_connection_resilience()` no-ops with a debug log when no cursor is available (standalone mode without backend wiring) instead of raising.

- **`src/sqlery/core/model_utils.py`:** added explicit `if django_models is None: raise RuntimeError(...)` guards at the top of `pydantic_to_django_model` and `map_pydantic_to_django_field` so callers get an informative error message instead of an opaque `AttributeError: 'NoneType' object has no attribute 'Model'` deep in the function body.

- **`src/sqlery/core/daemon_runner.py` + `src/sqlery/core/worker_runner.py`:** wrapped the in-function `import django; django.setup()` calls in `try/except ImportError` with an inline comment marking standalone mode as a no-op. The runners are now safely callable when Django is absent.

- **`src/sqlery/core/log_config.py`:** already correctly guarded; verified no-op. Line 54 `django_settings.BASE_DIR` is reachable only inside the `if is_django_mode():` branch, and the `else` branch already routes through `get_config('LOG_DIR', '/tmp/sqlery')`.

## Why

Phase 1 success criteria #1 was failing because `core/db_resilience.py` had a top-of-module unguarded `from django.db import ...`. That single line made `import sqlery.core` fatal in any environment without Django, blocking UNIF-03/04/05/06 and the standalone-mode CI job downstream. The remaining four files had partial guards that needed tightening for the audit grep to come back clean.

## Verification

```
$ grep -rnE '^(from|import) django' src/sqlery/core/*.py
(no output, exit 1)

$ PYTHONPATH=src python3 -c "import sqlery.core.db_resilience, sqlery.core.log_config, sqlery.core.model_utils, sqlery.core.daemon_runner, sqlery.core.worker_runner; print('IMPORTS OK')"
IMPORTS OK

$ PYTHONPATH=src python3 -c "import sys; sys.modules.pop('django', None); import sqlery.core.db_resilience as m; assert callable(m.retry_on_db_error); print('OK')"
OK
```

Also exercised the RuntimeError guards in `model_utils.py` by stubbing `django_models = None` and confirming both `pydantic_to_django_model` and `map_pydantic_to_django_field` raise `RuntimeError` whose message contains `"Django"`.

Django-mode regression test suite (`uv run pytest tests/ -k "db_resilience or retry" -x`) was **not** executed in this worktree because the worktree does not have the project venv / pytest dependencies installed. The verifier or the orchestrator should run it before merging; the changes preserve all public signatures (`retry_on_db_error`, `configure_connection_resilience`, `_configure_sqlite`, `_configure_postgresql`) and Django-mode call paths (Django branch of `_resolve_active_connection_and_vendor` returns the live `django.db.connection` exactly as before).

## Deviations from Plan

### Auto-fixed / planner-spec clarifications

**1. [Rule 3 - Blocking issue] `get_config` is a function, not a class accessor**
- **Found during:** Task 1 — plan's prose said "`get_config().get_setting(name, default)`", but `sqlery/compat/__init__.py` exposes `get_config(key, default)` as a module-level function (no `.get_setting` method).
- **Fix:** Wrapped the lookup in a local `_get_setting(name, default)` helper that calls `get_config(name, default)` directly, preserving the same call signature the rest of the module expects.
- **Files modified:** `src/sqlery/core/db_resilience.py`
- **Commit:** `1d67bdf`

**2. [Rule 2 - Missing critical functionality] standalone-mode `configure_connection_resilience` could crash on missing `vendor`**
- **Found during:** Task 1 — plan said "if `get_backend().vendor` raises `AttributeError`, log a debug message and return". Implemented via a `_resolve_active_connection_and_vendor()` helper that returns `(None, None)` on any exception path, plus an explicit "no cursor available → no-op" branch so the function never raises in standalone mode even if a backend exposes `vendor` but no cursor.
- **Commit:** `1d67bdf`

**3. [Plan spec note] `core/log_config.py` already met the acceptance criteria**
- The plan asked to potentially add an `elif` branch using `get_config("LOG_DIR", "./tmp")` for standalone mode. That branch already exists at line 56 (`Path(get_config('LOG_DIR', '/tmp/sqlery'))`). No change needed; left untouched.

### Out-of-scope discoveries (deferred — NOT fixed)

None.

### Architectural deviations (Rule 4)

None — all changes followed the plan's "Update callers, guarded imports" pattern.

## Known Stubs

None introduced by this plan. No placeholder UI, no hardcoded empty values flowing to rendering, no "TODO" wiring left dangling.

## Threat Flags

None. No new network endpoints, no new auth paths, no schema changes. The `_RETRYABLE_EXC` fallback to `(Exception,)` is module-internal (no untrusted input feeds it), matching the plan's `T-01-02 accept` disposition.

## Commits

| Hash | Title |
|------|-------|
| `1d67bdf` | fix(01-01): guard Django imports in core/db_resilience.py |
| `e80d3c0` | fix(01-01): tighten Django guards in core runner/model_utils |

## Self-Check: PASSED

- [x] `src/sqlery/core/db_resilience.py` modified — FOUND
- [x] `src/sqlery/core/model_utils.py` modified — FOUND
- [x] `src/sqlery/core/daemon_runner.py` modified — FOUND
- [x] `src/sqlery/core/worker_runner.py` modified — FOUND
- [x] commit `1d67bdf` — FOUND in `git log`
- [x] commit `e80d3c0` — FOUND in `git log`
- [x] `grep -rnE '^(from|import) django' src/sqlery/core/*.py` returns zero lines — VERIFIED
- [x] `import sqlery.core.db_resilience, sqlery.core.log_config, sqlery.core.model_utils, sqlery.core.daemon_runner, sqlery.core.worker_runner` succeeds — VERIFIED
