---
phase: 02-execution-modes
plan: 04
subsystem: async-backend-django
tags: [async, django, ormpath, ASYN-02]
requires: [ASYN-01, 02-02-status-shutting_down]
provides: [ASYN-02, DjangoAsyncBackend]
affects: [src/sqlery/django_sqlery/__init__.py]
tech_added: []
tech_patterns: [native-async-django-orm, raw-acursor-skiplocked, version-CAS]
files_created:
  - src/sqlery/django_sqlery/async_backend.py
  - tests/test_django_async_backend.py
files_modified:
  - src/sqlery/django_sqlery/__init__.py
decisions:
  - "Adopted [ASSUMED §A2] option 1 (raw acursor BEGIN/COMMIT) because Django 5.2.14 does NOT ship transaction.aatomic (verified at implementation time via hasattr check)."
  - "SQLite path uses single-statement version-CAS via aupdate() — atomic without needing any transaction wrapper."
  - "DjangoAsyncBackend exported lazily from sqlery.django_sqlery via module-level __getattr__ to honor the existing 'no eager imports in __init__.py' contract."
metrics:
  duration_minutes: ~25
  completed: 2026-05-13
  tests_added: 19
  tests_passing: 19
---

# Phase 02 Plan 04: DjangoAsyncBackend Summary

**One-liner:** Implements `DjangoAsyncBackend(AsyncDatabaseBackend)` against Django 5.2 native async ORM with raw `acursor()` SKIP LOCKED on Postgres and version-CAS on SQLite — zero thread-offload helpers.

## Tasks Completed

| Task | Name | Commit | Files |
| ---- | --------------------------------------- | -------- | ----- |
| 1 (RED) | Failing tests for every async method | 629b989  | tests/test_django_async_backend.py |
| 1 (GREEN) | Implement DjangoAsyncBackend         | b384d70  | src/sqlery/django_sqlery/async_backend.py, src/sqlery/django_sqlery/__init__.py |

## What Was Built

`DjangoAsyncBackend` implements every method declared on `AsyncDatabaseBackend` (ASYN-01) using exclusively native async ORM calls:

- **Hot claim path** (`aclaim_job`) branches on `connection.vendor`:
  - **PostgreSQL** → raw `await connection.acursor()` with explicit `BEGIN`/`COMMIT`/`ROLLBACK` wrapping a `SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1` followed by an `UPDATE`. Django 5.2 lacks `transaction.aatomic`, so raw transaction control is the only fully-async option that obeys the project rule.
  - **SQLite** → single-statement optimistic concurrency: `QueuedJob.objects.filter(pk, version=N).aupdate(version=N+1, status='running', started_at=now)`. Returns the claim only when `rowcount == 1`.
- **Terminal-status writes** (`amark_running`, `amark_success`, `amark_failed`, `amark_shutting_down`) use `filter(pk).aupdate(...)`.
- **Reads** (`aget_status`, `aget_job`) use `.aget()` with `DoesNotExist → None`.
- **Worker registry** (`aregister_worker`, `aunregister_worker`, `aupdate_heartbeat`) use `aupdate_or_create()`, `adelete()`, and a single `aupdate(last_heartbeat=now)`.
- **Leases** (`aclaim_lease`, `arenew_lease`, `arelease_lease`) use a two-step pattern (`aupdate` over expired-or-self rows, then `acreate` with `IntegrityError` fallback) — no `aatomic` required since both attempts are single statements.
- **Scheduler** (`aget_due_scheduled_tasks`) consumes the queryset via `async for` into a list.
- **Registry** (`aregistry_add`, `aregistry_remove`) use `.acreate()` and `filter(...).aupdate(exited_at=now)`.

`timezone.now()` is captured **before** every `await` that uses the timestamp (RESEARCH §9 pitfall).

`DjangoAsyncBackend` is exported from `sqlery.django_sqlery` via a module-level `__getattr__` to keep the package `__init__.py` free of eager imports (preserving the existing app-loading contract).

## Verification

```
$ PYTHONPATH=. uv run pytest tests/test_django_async_backend.py
19 passed, 1 warning in 0.37s

$ grep -c sync_to_async src/sqlery/django_sqlery/async_backend.py
0

$ grep -c acursor src/sqlery/django_sqlery/async_backend.py
5
```

All three plan-level verification gates pass (`pytest`, `grep -c sync_to_async == 0`, `grep -c acursor >= 1`).

The test suite includes a concurrency test using `asyncio.gather` on two simultaneous `aclaim_job` calls against a single queued row — exactly one caller wins (the CAS loser returns `None`).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 — Blocker] Self-test docstring contained the forbidden substring**
- **Found during:** Task 1 GREEN run
- **Issue:** The plan's "done" criterion is `! grep sync_to_async src/sqlery/django_sqlery/async_backend.py`. My initial module docstring referenced the rule "no `sync_to_async`" by name, which the static check flagged.
- **Fix:** Reworded two docstring strings to describe the rule without naming the helper (`"native async, no thread offload"`).
- **Files modified:** `src/sqlery/django_sqlery/async_backend.py`
- **Commit:** rolled into `b384d70` (no separate commit since the GREEN gate had not yet passed).

### Authentication Gates

None.

### Architectural Decisions

None requiring user input. The `transaction.aatomic` availability check was pre-specified in the plan's `<assumptions>` block as a runtime branch — I confirmed Django 5.2.14 does NOT ship `aatomic` and selected option 1 (raw acursor BEGIN/COMMIT) as the plan's preferred fallback.

## Known Stubs

None. Every method has a real implementation that exercises the DB.

## Threat Flags

None. The new code does not introduce new network endpoints, auth paths, or trust-boundary schema changes; it implements an alternate async path against existing tables already exposed by the sync backend.

## Self-Check

- [x] `src/sqlery/django_sqlery/async_backend.py` exists
- [x] `tests/test_django_async_backend.py` exists
- [x] Commit `629b989` (RED) reachable from HEAD
- [x] Commit `b384d70` (GREEN) reachable from HEAD
- [x] `grep -c sync_to_async src/sqlery/django_sqlery/async_backend.py` returns 0
- [x] `grep -c acursor src/sqlery/django_sqlery/async_backend.py` returns 5 (≥ 1)
- [x] 19/19 tests pass on SQLite

## Self-Check: PASSED
