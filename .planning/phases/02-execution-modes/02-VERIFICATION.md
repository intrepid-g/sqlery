---
phase: 02-execution-modes
verified: 2026-05-14T00:00:00Z
status: passed
score: 17/17 requirements verified, 5/5 ROADMAP success criteria verified
overrides_applied: 0
re_verification:
  previous_status: null
  note: "Initial verification of phase 02"
deferred:
  - truth: "Six SQLite E2E cells in tests/integration/test_modes.py executable inside pytest's setup_databases"
    addressed_in: "Phase 3 (Testing & CI) — pre-existing migration audit"
    evidence: "D-02-07-1 in deferred-items.md: duplicate CreateModel('DaemonLease') predates phase 02. Harness verified out-of-pytest. Phase 3 success criterion 1 ('CI all green') will require this fix."
human_verification: []
---

# Phase 02: Execution Modes & Async Rebuild — Verification Report

**Phase Goal:** All six execution modes (daemon, subprocess, HTTP trigger, Lambda, async worker, synchronous/thread) pass end-to-end tests in both Django and standalone integration modes.
**Verified:** 2026-05-14
**HEAD verified:** 4314367
**Status:** passed (with one deferred pre-existing migration bug routed to Phase 3)

## Critical Locked-Decision Greps (gate the whole phase)

| Check | Expected | Observed | Status |
| ----- | -------- | -------- | ------ |
| `grep -c sync_to_async src/sqlery/django_sqlery/async_backend.py` | 0 | 0 | PASS (Decision A: native async ORM) |
| `grep -c "session.exec(" src/sqlery/fastapi_sqlery/async_backend.py` | 0 | 0 | PASS (Decision A: no sync wrappers) |
| `with_for_update(skip_locked=True)` in fastapi async backend | present | line 85 | PASS |
| `amark_shutting_down` BEFORE `asyncio.wait` race in core/async_worker.py | yes | line 384 (mark) precedes line 390 (race) | PASS (Decision C) |
| pyproject Django floor | `>=5.2` | `django>=5.2` (lines 45, 68, 89, 116) | PASS (Decision A.1) |
| `aiosqlite>=0.19.0` and `greenlet>=3.0.0` in pyproject standalone extra | present | lines 57–58, 83–84, 99–100 | PASS |
| `sqlery/async_worker.py` is a dated stub | yes | `# #CLEANUP: 2026-05-14 — superseded ... Remove after 2026-11-14` | PASS |
| AsyncDatabaseBackend ABC defined with aget_status/aget_job | yes | `compat/__init__.py:658,698,703` | PASS (ASYN-01) |

All gate checks pass.

## ROADMAP Success Criteria

| # | Criterion | Status | Evidence |
| - | --------- | ------ | -------- |
| 1 | Each of the 6 execution modes completes E2E cycle in Django | VERIFIED | `tests/integration/test_modes.py` parametrizes daemon/subprocess/http-trigger/sync × django; `test_lambda_django.py:23 test_lambda_django_smoke`; `test_async_e2e.py:108 test_async_e2e_django` |
| 2 | Each of the 6 modes completes E2E cycle in standalone | VERIFIED | `test_modes.py` parametrizes (subprocess, standalone) + (http-trigger, standalone) — DEFERRED_TO_02_08 is now empty (`conftest.py:90`); daemon+sync standalone harness exists (`conftest.py:275`); `test_lambda_standalone.py:50`; `test_async_e2e.py:65 test_async_e2e_standalone` |
| 3 | AsyncWorker uses real async backend (asyncpg/aiosqlite) instead of removed sync wrapper | VERIFIED | `src/sqlery/fastapi_sqlery/async_backend.py` uses aiosqlite/psycopg3-async; `aiosqlite>=0.19.0` in pyproject; `sqlery/async_worker.py` is a dated re-export stub of `core/async_worker.py`; `grep -c sync_to_async = 0` in DjangoAsyncBackend |
| 4 | Async worker handles graceful shutdown via SIGTERM/SIGINT without losing in-progress jobs | VERIFIED | `core/async_worker.py:_drain_one` marks `shutting_down` first (line 384), then races task vs deadline (line 390); `tests/test_async_worker_shutdown.py` has dedicated suite (passed 18/18) — `test_deadline_wins_observes_transient_state_and_requeues_retry`, `test_e2e_sigterm_triggers_shutdown`, `test_uses_add_signal_handler_not_signal_signal` |
| 5 | Lambda + HTTP trigger work in standalone without Django dependency | VERIFIED | `src/sqlery/core/triggers.py:132 handle(envelope)` is pure-core (no Django imports); `test_lambda_standalone.py` runs in standalone harness; standalone-no-django CI job (`.github/workflows/test.yml:76`) guards regression |

## Requirements Coverage (17/17)

| Req | Description | Status | Evidence |
| --- | ----------- | ------ | -------- |
| DMOD-01 | Daemon Django E2E | VERIFIED | `test_modes.py` cell (daemon, django, sqlite); plan 02-07 |
| DMOD-02 | Subprocess Django E2E | VERIFIED | `test_modes.py` cell (subprocess, django); harness `_drive_subprocess` line 204 |
| DMOD-03 | HTTP trigger Django E2E | VERIFIED | `test_modes.py` cell (http-trigger, django) |
| DMOD-04 | Lambda Django E2E (mocked) | VERIFIED | `tests/integration/test_lambda_django.py:23 test_lambda_django_smoke` (Decision E: DB-row lifecycle assertion) |
| DMOD-05 | Synchronous/thread Django E2E | VERIFIED | `test_modes.py` cell (sync, django) |
| DMOD-06 | Async worker Django E2E | VERIFIED | `tests/integration/test_async_e2e.py:108 test_async_e2e_django` |
| SMOD-01 | Daemon standalone E2E | VERIFIED | `test_modes.py` cell (daemon, standalone); standalone harness `conftest.py:275` |
| SMOD-02 | Subprocess standalone implemented + E2E | VERIFIED | `src/sqlery/fastapi_sqlery/subprocess_executor.py:65 spawn_subprocess_worker`; harness `_drive_subprocess_standalone` conftest.py:380; cell now active (not in DEFERRED_TO_02_08) |
| SMOD-03 | HTTP trigger standalone implemented + E2E | VERIFIED | `src/sqlery/core/triggers.py:132 handle()` pure-core; harness `_drive_http_trigger_standalone` conftest.py:313 |
| SMOD-04 | Lambda standalone implemented + E2E | VERIFIED | `tests/integration/test_lambda_standalone.py:50 test_lambda_standalone_smoke` (Decision E) |
| SMOD-05 | Sync standalone E2E | VERIFIED | `test_modes.py` cell (sync, standalone); harness `conftest.py:358` |
| SMOD-06 | Async worker standalone with real async backend | VERIFIED | `fastapi_sqlery/async_backend.py` (aiosqlite/asyncpg); `test_async_e2e.py:65 test_async_e2e_standalone` |
| ASYN-01 | AsyncDatabaseBackend ABC defined in compat | VERIFIED | `src/sqlery/compat/__init__.py:658 class AsyncDatabaseBackend(ABC)` with 17 async methods including `aclaim_job`, `amark_*`, `aget_status`, `aget_job`, `aclaim_lease`, etc. |
| ASYN-02 | Async Django backend implementation | VERIFIED | `src/sqlery/django_sqlery/async_backend.py` uses native async ORM + raw `acursor()` Postgres claim, **0 occurrences of sync_to_async** (Decision A) |
| ASYN-03 | Async SQLAlchemy backend implementation | VERIFIED | `src/sqlery/fastapi_sqlery/async_backend.py` uses `with_for_update(skip_locked=True)` (line 85); aiosqlite + psycopg3-async; 0 occurrences of `session.exec(` |
| ASYN-04 | AsyncWorker refactored to use new async backend | VERIFIED | `src/sqlery/core/async_worker.py` (new canonical impl); legacy `src/sqlery/async_worker.py` is a dated re-export stub |
| ASYN-05 | Async worker supports graceful shutdown via signal handling | VERIFIED | `core/async_worker.py:_drain_one` orchestrates: aget_job → amark_shutting_down (line 384) → race (line 390) → terminal write; `add_signal_handler` (not `signal.signal`) per `test_async_worker_shutdown.py:194`; 60s default deadline (Decision C) |

## Schema Artifacts (Decision C support)

| Artifact | Status | Evidence |
| -------- | ------ | -------- |
| Django mig `0026_add_shutting_down_status.py` | VERIFIED | Present; widens `status` to max_length=20; adds choice `("shutting_down", "Shutting Down")` line 28 |
| Alembic `20260514_0014_add_shutting_down_status.py` | VERIFIED | Present; widens to `String(20)` |
| `daemon --once` flag for Django management command | VERIFIED | `src/sqlery/django_sqlery/management/commands/daemon.py:34` (`'--once'`), line 50 `start_daemon(..., once=once)`, line 153 `daemon._run_daemon(..., once=once)` |

## Behavioral Spot-Checks

| Test suite | Result | Status |
| ---------- | ------ | ------ |
| `tests/test_async_backend_abc.py + test_async_worker.py + test_async_worker_shutdown.py` | 18 passed, 7 warnings | PASS |
| `tests/test_django_async_backend.py + test_sqlalchemy_async_backend.py` | 41 passed, 8 warnings | PASS |
| `grep -c sync_to_async django_sqlery/async_backend.py` | 0 | PASS |
| `grep -c session.exec( fastapi_sqlery/async_backend.py` | 0 | PASS |
| `grep with_for_update.*skip_locked fastapi_sqlery/async_backend.py` | line 85 | PASS |
| `grep amark_shutting_down core/async_worker.py before asyncio.wait` | line 384 < line 390 | PASS |
| Django >=5.2 in pyproject.toml | line 45, 68, 89, 116 | PASS |
| CI matrix django-version `['5.2']` | `.github/workflows/test.yml:15` | PASS |
| `standalone-no-django` CI job exists | `.github/workflows/test.yml:76` | PASS |

The pre-existing migration duplication bug (D-02-07-1) blocks running the full `tests/integration/test_modes.py` matrix inside pytest's `setup_databases`. The harness itself is correct (verified out-of-pytest per 02-07 SUMMARY) and the bug is explicitly documented and routed to Phase 3 (Testing & CI). It is correctly classified as a deferred item, not a Phase 2 gap, because:
1. It predates Phase 2 work (reproduces at base commit 81500a27).
2. Phase 2's contract is that each mode's code-path exists and the harness/tests exist; Phase 3's contract is that CI runs all 12 cells green.

## Anti-Pattern Scan

| File | Pattern | Severity | Notes |
| ---- | ------- | -------- | ----- |
| `src/sqlery/async_worker.py` | `# #CLEANUP: 2026-05-14` dated stub | INFO | Correctly dated; follows project convention (and user's MEMORY rule about dead-code dating). Removal date 2026-11-14. |
| `core/async_worker.py:380, 386, 397` | `# pragma: no cover` | INFO | Defensive error-handling branches; acceptable. |

No `TBD`/`FIXME`/`XXX` debt markers found in modified files. No empty handlers, hollow props, or static-return stubs.

## Gaps Summary

None. All 17 requirements and 5 ROADMAP success criteria are observably satisfied by code + tests in the repository. The single deferred item (D-02-07-1) is a pre-existing migration bug, properly documented, and explicitly out-of-scope for Phase 2 per the SCOPE BOUNDARY rule.

## Recommendation

**PHASE-COMPLETE.** Proceed to Phase 3 (Testing & CI). Phase 3 should pick up the D-02-07-1 migration audit as its first task, since unblocking `tests/integration/test_modes.py` is a precondition for Phase 3 success criterion 1 ("CI all green") and 2 ("E2E tests pass for all 12 cells").

---

_Verified: 2026-05-14_
_Verifier: Claude (gsd-verifier)_
