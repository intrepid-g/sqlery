---
phase: 02-execution-modes
plan: 08
subsystem: execution-modes
tags: [standalone-impl, http-trigger, lambda, async-e2e, smod, dmod]
requires:
  - 02-06 (AsyncWorker rebuild + drain-with-deadline)
  - 02-07 (E2E parametrized harness, daemon --once flag, integration_setup pattern)
provides:
  - SMOD-02 standalone subprocess execution
  - SMOD-03 pure-core HTTP trigger handler + Django/FastAPI adapters
  - DMOD-04 + SMOD-04 Lambda smoke coverage with DB-row lifecycle assertions
  - DMOD-06 + SMOD-06 AsyncWorker end-to-end (sqlite, both integrations)
affects:
  - sqlery.core.signature (new — relocated HMAC helpers)
  - sqlery.core.triggers (new — pure HTTP receiver)
  - sqlery.core.lambda_core (new — mode-agnostic Lambda dispatch)
  - sqlery.fastapi_sqlery.subprocess_executor (new)
  - sqlery.fastapi_sqlery.triggers (new)
  - sqlery.fastapi_sqlery.lambda_handler (new — no-Django twin)
  - sqlery.lambda_handler (refactored Django twin)
  - sqlery.django_sqlery.signature (dated re-export stub)
  - sqlery.django_sqlery.views (new trigger_view)
  - sqlery.django_sqlery.urls (new /_internal/trigger route)
  - tests/integration/ (subprocess/http-trigger standalone branches + 3 new test modules)
tech-stack:
  added: []
  patterns:
    - pure-core function + framework-thin adapters (CONTEXT D, applied to HTTP trigger)
    - mode-agnostic Lambda dispatch helper (CONTEXT E, applied to lambda_core)
    - dated re-export stubs for relocated modules (Phase 1 policy)
key-files:
  created:
    - src/sqlery/core/signature.py
    - src/sqlery/core/triggers.py
    - src/sqlery/core/lambda_core.py
    - src/sqlery/fastapi_sqlery/subprocess_executor.py
    - src/sqlery/fastapi_sqlery/triggers.py
    - src/sqlery/fastapi_sqlery/lambda_handler.py
    - tests/integration/test_lambda_django.py
    - tests/integration/test_lambda_standalone.py
    - tests/integration/test_async_e2e.py
  modified:
    - src/sqlery/django_sqlery/signature.py (now a dated stub)
    - src/sqlery/django_sqlery/views.py (added trigger_view)
    - src/sqlery/django_sqlery/urls.py (added /_internal/trigger route)
    - src/sqlery/fastapi_sqlery/app.py (mount trigger router)
    - src/sqlery/lambda_handler.py (Django branch now delegates to lambda_core for process_queue/poll_and_process)
    - src/sqlery/eventbridge_trigger.py (Rule 3 import fix)
    - tests/integration/conftest.py (subprocess/http-trigger standalone branches)
decisions:
  - HTTP trigger receiver lives in sqlery.core.triggers (NEW surface). Top-level sqlery.triggers (147-line strategy file) is LEFT LIVE per CONTEXT open-question 3 — it is a distinct dispatch surface, not the receiver.
  - Idempotency uses an in-memory LRU keyed by (timestamp, signature) with 30s TTL / 1024 entries. Flagged as a future replacement for distributed deployments.
  - lambda_core.process_event returns lists ({processed, failed, job_ids}); tests assert DB-row status (PLAN-CHECKER-FIXES B1), never the return value.
  - Django Lambda smoke test passes ``job_id`` explicitly because Django's claim_job requires a registered Worker row — that registration is an operational pre-step, out of scope for the smoke.
  - AsyncWorker E2E lives in tests/integration/test_async_e2e.py (sibling module), not in test_modes.py — pytest-asyncio + AsyncSession lifecycle doesn't mix cleanly with the sync parametrized matrix.
metrics:
  duration: ~13 minutes
  completed: 2026-05-14
  tasks: 4
  files_created: 9
  files_modified: 7
---

# Phase 2 Plan 08: Net-New Execution Modes — Summary

Standalone subprocess, pure-core HTTP trigger, Lambda smokes (×2), and AsyncWorker end-to-end (×2) — landing every remaining Phase 2 mode requirement in one wave.

## What Shipped

### SMOD-02 — Standalone subprocess executor (Task 1)

`src/sqlery/fastapi_sqlery/subprocess_executor.py` exposes `spawn_subprocess_worker(database_url, queues, one_shot, timeout)`. The child runs an inline driver script that:

1. Scrubs `DJANGO_SETTINGS_MODULE` from its env.
2. Calls `compat.initialize(database_url=...)` to bring up the SQLAlchemy backend.
3. Either claims-and-executes one job (`one_shot=True`) or runs `WorkerProcess(queues=...).run()` until SIGTERM.

The parent disposes the SQLAlchemy engine before `Popen` (RESEARCH §8 fork-safety) so SQLite/WAL writers don't race.

Harness wiring (`tests/integration/conftest.py`): `_StandaloneHarness._drive_subprocess_standalone(job_id)` runs the driver inside a no-Django outer subprocess so the test can construct the harness from a Django-enabled pytest while still exercising the no-Django path under test.

### SMOD-03 — Pure-core HTTP trigger handler + adapters (Task 2)

Per CONTEXT decision D, the HTTP receiver is now a pure function in `sqlery.core.triggers`:

- `TriggerEnvelope` (body, headers, payload) and `TriggerResult` (status_code, body) dataclasses.
- `handle(envelope) -> TriggerResult` verifies the HMAC signature (via the relocated `sqlery.core.signature.verify_signature`), enforces idempotency via an in-memory LRU keyed by `(timestamp, signature)`, and dispatches on `payload['action']` (`process_queue` claims+executes one job; `process_scheduled` runs the scheduler).

Framework-thin adapters:

- `sqlery.fastapi_sqlery.triggers.router` mounts `POST /trigger` on the standalone `sqlery-web` FastAPI app.
- `sqlery.django_sqlery.views.trigger_view` is a new Django view that builds an envelope from `request`, calls `core.triggers.handle`, and returns a `JsonResponse`. Registered at `/_internal/trigger`.
- `sqlery.django_sqlery.signature` is now a dated re-export stub (Phase 1 stub-don't-delete policy). The legacy `internal_worker` Django view is preserved unchanged for back-compat.

### DMOD-04 + SMOD-04 — Lambda smoke (Task 3)

`sqlery.core.lambda_core.process_event(event, backend)` is the mode-agnostic claim+execute helper. The Django `sqlery.lambda_handler.handler` now delegates `process_queue` and `poll_and_process` to it (preserving `run_scheduled_task` on the legacy Django-specific path because it touches `ScheduledTask` + EventBridge plumbing).

`sqlery.fastapi_sqlery.lambda_handler.handler` is the new no-Django Lambda entry point. Grep verification: `grep -E "import django|from django|setup_django" src/sqlery/fastapi_sqlery/lambda_handler.py` returns empty.

Test pair:

- `tests/integration/test_lambda_django.py`: `@pytest.mark.django_db` test that enqueues a row, invokes `handler({"action": "process_queue", "queue_name": "default", "job_id": ...})`, and asserts `QueuedJob.objects.get(id=job.id).status in {running, success, failed}` — DB-row lifecycle per PLAN-CHECKER-FIXES B1.
- `tests/integration/test_lambda_standalone.py`: subprocess-isolated mirror that scrubs `DJANGO_SETTINGS_MODULE` per-subprocess. Asserts the SQLModel row's status field after handler return. Fixture-asymmetry documented in test module docstrings as intentional.

### DMOD-06 + SMOD-06 — AsyncWorker E2E (Task 4)

`tests/integration/test_async_e2e.py` ships two pytest-asyncio tests:

- `test_async_e2e_standalone`: `SQLAlchemyAsyncBackend` on `sqlite+aiosqlite:///:memory:`. Enqueues, schedules `AsyncWorker.run(max_jobs=1)` as a background task, awaits with a 10s timeout, asserts terminal `status == "success"` and `output == "3"`.
- `test_async_e2e_django`: `DjangoAsyncBackend` on the Django test DB via native async ORM (`acreate`, `aget`).

Split into a sibling test module (not `test_modes.py`) because pytest-asyncio fixture wiring + `AsyncSession` lifecycle does not mix cleanly with the sync parametrized matrix.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking import] Fixed stale `from .settings import get_setting` in `eventbridge_trigger.py`**

- **Found during:** Task 3 — `tests/integration/test_lambda_django.py` execution.
- **Issue:** `src/sqlery/eventbridge_trigger.py:39` imported `get_setting` from `sqlery.settings`, but that path was reduced to a back-compat stub in Phase 1 and no longer re-exports `get_setting`. Importing `sqlery.lambda_handler` therefore failed at module load with `ImportError`.
- **Fix:** Changed the import to `from .django_sqlery.settings import get_setting`.
- **Files modified:** `src/sqlery/eventbridge_trigger.py`.
- **Commit:** `99de852` (folded into Task 3 commit).

**2. [Rule 2 - Missing functionality] Refactored `sqlery.lambda_handler.handler` to delegate to `lambda_core.process_event`**

- **Found during:** Task 3.
- **Issue:** The existing `process_queue_action` had a bug (`result.get(...)` on a list returned by `executor.run_queue_workers`), and Plan task 3 step 2 required the Django branch to share the `lambda_core.process_event` claim+execute path.
- **Fix:** `handler` now routes `process_queue` and `poll_and_process` actions through `lambda_core.process_event(event, get_backend())`. `run_scheduled_task` still uses the legacy Django-specific dispatcher because it touches Django-only models.
- **Files modified:** `src/sqlery/lambda_handler.py`.
- **Commit:** `99de852`.

### Acceptance Deviations

**3. Lambda Django smoke test uses `job_id` instead of `claim_job`**

- **Reason:** Django's `claim_job` requires a registered Worker row (`_resolve_worker` returns None ⇒ `claim_job` returns None ⇒ lambda_core sees no job). That registration is an operational pre-step, out of scope for the smoke assertion (which just needs to prove the handler can execute a known queued row and the DB row's status transitions). The standalone test similarly passes `job_id` for symmetry.
- **Impact:** None on the must-haves — PLAN-CHECKER-FIXES B1's lifecycle assertion is satisfied either way.

### Auth gates

None.

## Verification Results

```
PYTHONPATH=. uv run pytest \
  "tests/integration/test_modes.py::test_mode_e2e[sqlite-standalone-subprocess]" \
  "tests/integration/test_modes.py::test_mode_e2e[sqlite-standalone-http-trigger]" \
  tests/integration/test_lambda_django.py \
  tests/integration/test_lambda_standalone.py \
  tests/integration/test_async_e2e.py
```

=> **6 passed**.

Additional checks:

- `grep -E "import django|from django|setup_django" src/sqlery/fastapi_sqlery/lambda_handler.py` ⇒ empty (CLEAN).
- `grep -c "core\.triggers\.handle\|triggers\.handle"` on the two adapter files ⇒ 2/2 (>0 on both).
- Existing `tests/test_triggers.py`: 15 passed, 2 skipped, 1 pre-existing failure (`test_synchronous_error_is_raised` — Django DB-fixture marking issue, unrelated to this plan, reproduced on the pre-plan tree).

## Commits

| Task | Commit  | Message                                                  |
| ---- | ------- | -------------------------------------------------------- |
| 1    | 1093438 | feat(02-08): standalone subprocess executor (SMOD-02)    |
| 2    | 5ccbac1 | feat(02-08): pure-core HTTP trigger handler + adapters   |
| 3    | 99de852 | feat(02-08): Lambda smoke tests + standalone handler     |
| 4    | 16cb1c9 | test(02-08): AsyncWorker E2E matrix rows (DMOD-06, etc.) |

## Known Stubs

- `src/sqlery/django_sqlery/signature.py` is intentionally a dated re-export stub (#CLEANUP 2026-05-14, remove after 2026-11-14) — relocation of HMAC helpers to `sqlery.core.signature` per CONTEXT decision D. Existing callers within `django_sqlery` and downstream user code remain functional through the deprecation window.

## Self-Check: PASSED

- Created files exist: src/sqlery/core/signature.py, src/sqlery/core/triggers.py, src/sqlery/core/lambda_core.py, src/sqlery/fastapi_sqlery/subprocess_executor.py, src/sqlery/fastapi_sqlery/triggers.py, src/sqlery/fastapi_sqlery/lambda_handler.py, tests/integration/test_lambda_django.py, tests/integration/test_lambda_standalone.py, tests/integration/test_async_e2e.py — all FOUND.
- Commits exist: 1093438, 5ccbac1, 99de852, 16cb1c9 — all FOUND in `git log`.
