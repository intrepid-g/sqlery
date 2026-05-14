---
phase: 02-execution-modes
plan: 06
subsystem: async-worker
tags: [async, worker, shutdown, signals, asyn-04, asyn-05]
requires: [02-02, 02-04, 02-05]
provides: [AsyncWorker (rewritten), drain-with-deadline shutdown]
affects: [src/sqlery/async_worker.py, src/sqlery/core/async_worker.py]
tech_added:
  - "asyncio.add_signal_handler-based SIGTERM/SIGINT wiring"
  - "drain-with-deadline race via asyncio.wait(FIRST_COMPLETED)"
patterns:
  - "Transient-state-before-race shutdown (write 'shutting_down' BEFORE asyncio.wait)"
  - "Bounded run() via max_jobs / max_polls for deterministic tests"
key_files:
  created:
    - src/sqlery/core/async_worker.py
    - tests/test_async_worker.py
    - tests/test_async_worker_shutdown.py
  modified:
    - src/sqlery/async_worker.py (now a dated stub)
decisions:
  - "Decision C (drain-with-deadline) implemented exactly: transient state written BEFORE the race, never interleaved."
  - "Retry-requeue on shutdown failure uses SQLAlchemyAsyncBackend's session factory directly; Django async retry helper is a known follow-up."
metrics:
  duration_minutes: ~45
  tasks_completed: 3
  tests_added: 13
  completed: 2026-05-14
---

# Phase 02 Plan 06: AsyncWorker rewrite + drain-with-deadline shutdown Summary

Rewrote the async worker (broken since v0.13) on top of the new `AsyncDatabaseBackend` ABC and implemented the decision-C drain-with-deadline shutdown semantics in one cohesive plan (per RESEARCH §F wave 3).

## What was built

- **`src/sqlery/core/async_worker.py`** — new `AsyncWorker` class (≈ 360 lines):
  - Constructor: `AsyncWorker(backend, queues=['default'], worker_id=None, poll_interval=1.0, shutdown_deadline_seconds=None)`.
  - Reads `SQLERY_ASYNC_SHUTDOWN_DEADLINE_SECONDS` env (default 60s) when no kwarg given.
  - `run(max_jobs=None, max_polls=None)` — poll loop with bounded-run controls for tests.
  - Async-defined jobs run as `asyncio.create_task(coro)`; sync-defined jobs offloaded via `loop.run_in_executor(None, fn, ...)`.
  - Poll loop wraps `await task` in an `asyncio.wait({task, shutdown_event})` so SIGTERM interrupts a long-running await instead of waiting for the job to naturally complete.
  - Heartbeat update + claim per poll cycle.
  - Retry-on-failure honors `max_retries > 0` (currently wired for `SQLAlchemyAsyncBackend`).
  - Signal handlers installed exclusively via `loop.add_signal_handler` — never `signal.signal`.
  - `_drain_with_deadline` iterates in-flight jobs and, for each:
    1. Writes the transient `shutting_down` state via `backend.amark_shutting_down(job_id)` BEFORE the race.
    2. Races `task` against `asyncio.sleep(self.shutdown_deadline_seconds)` via `asyncio.wait(return_when=FIRST_COMPLETED)`.
    3. Job-wins → normal terminal write (success/failed); transient state overwritten.
    4. Deadline-wins → cancel task, mark `failed` with `SHUTDOWN_TIMEOUT_ERROR`, requeue if `max_retries > 0`.
  - Worker id default uses `uuid7()` (UUID instance — round-trips through `Worker.id` UUID column).

- **`src/sqlery/async_worker.py`** — converted to a dated `#CLEANUP: 2026-05-14` stub that re-exports `AsyncWorker` and `SHUTDOWN_TIMEOUT_ERROR` from `sqlery.core.async_worker`. Old contents preserved in git history.

- **`tests/test_async_worker.py`** — 7 tests:
  - Constructor defaults + overrides.
  - Async happy path (asyncio.create_task).
  - Sync happy path (run_in_executor).
  - Failure path records error + traceback.
  - Failure path with `max_retries=2` enqueues a retry row with `parent_job_id` and incremented `retry_count`.
  - Heartbeat called once per poll cycle.
  - Static guard: source contains no `signal.signal(` calls.

- **`tests/test_async_worker_shutdown.py`** — 6 tests (one `@pytest.mark.slow`):
  - Job-wins-before-deadline → marked `success` with real result. (Transient state intentionally not asserted on this path.)
  - **Deadline-wins → transient `shutting_down` IS observable from a second async session/backend** (the load-bearing contract from plan-checker fix W2). After the race, row ends `failed` with the canonical `shutdown_timeout: worker terminated before job finished` error and a retry row is enqueued.
  - `_initiate_shutdown` is idempotent.
  - Static guard: `loop.add_signal_handler(` present, `signal.signal(` absent.
  - Static guard: canonical error literal appears exactly once.
  - **E2E SIGTERM via `os.kill(os.getpid(), SIGTERM)`** marked `@pytest.mark.slow` (per plan's CI-flakiness mitigation).

## Verification

```bash
PYTHONPATH=. uv run pytest tests/test_async_worker.py tests/test_async_worker_shutdown.py
# => 13 passed in ~0.7s
grep -rE "signal\.signal\(" src/sqlery/core/async_worker.py src/sqlery/async_worker.py  # => no matches
grep -c "shutdown_timeout: worker terminated" src/sqlery/core/async_worker.py            # => 1
grep -c "amark_shutting_down" src/sqlery/core/async_worker.py                            # => 4
```

All `<verification>` checks from the plan pass.

## Deviations from Plan

### Auto-fixed issues

**1. [Rule 3 — Blocking issue] Worker poll loop blocked on `await task`, drain never reached on shutdown**
- **Found during:** Task 2 (deadline-wins test failed first run).
- **Issue:** The original `_execute_job` did a plain `await task`, so a SIGTERM during a long-running job left the poll loop blocked until the job naturally completed — drain never ran.
- **Fix:** Added an `asyncio.Event` (`self._shutdown_event`) set by `_initiate_shutdown`, and changed `_execute_job` to `asyncio.wait({task, shutdown_event_wait}, FIRST_COMPLETED)`. On shutdown, `_execute_job` returns early leaving the task in `self._inflight`, and `_drain_with_deadline` (in the run-loop `finally`) takes over the terminal write.
- **Commit:** `ac0488c`.

**2. [Rule 1 — Bug] uuid7 worker id couldn't write to `Worker.id` UUID column**
- **Found during:** Task 1 heartbeat test.
- **Issue:** Original `_generate_worker_id` returned `"async-worker-{uuid7()}"` (a string), but `Worker.id` is `UUID` and SQLAlchemy's UUID column type strips str. Heartbeat / register couldn't find/match rows.
- **Fix:** `_generate_worker_id` now returns a `UUID` instance (`uuid6.uuid7()` directly), matching the sync `Worker.id` column type.
- **Commit:** `1e58f53`.

**3. [Rule 1 — Bug] Verification grep failed on docstring**
- **Found during:** Verification.
- **Issue:** Plan asserts `grep -c "shutdown_timeout: worker terminated" == 1`; module had the literal once in the constant and once in a docstring.
- **Fix:** Rewrote the docstring to reference `:data:\`SHUTDOWN_TIMEOUT_ERROR\`` instead of inlining the literal.
- **Commit:** `df77e70`.

### Assumptions / known follow-ups

- **Retry-on-failure for `DjangoAsyncBackend` is not yet wired** (logs a warning and skips). The async ABC does not yet expose `acreate_retry_job`; Django retry today is a sync path. Tracked as a Phase 2 follow-up (does not block ASYN-04/05 success criteria — the plan scopes test coverage to `SQLAlchemyAsyncBackend`).
- **`peeker` polling rate** in the deadline-wins test was reduced from 100Hz to 200Hz with early-return on first `shutting_down` sighting after observing contention with SQLite in-memory writes. Documented inline in the test.

## Threat Flags

None — no new external surface (no endpoints, no schema, no auth path). The `shutting_down` row state was added to the schema in plan 02-04.

## TDD Gate Compliance

- RED commit: `2a75720` (`test(02-06): add failing tests for AsyncWorker rewrite (RED)`).
- GREEN commit: `1e58f53` (`feat(02-06): rewrite AsyncWorker ...`).
- Task 2 RED/GREEN landed in a single commit (`ac0488c`) because the shutdown logic was already scaffolded by Task 1 — tests written first, observed failure (drain never reached, see deviation #1 above), then implementation extended. Reconstructable from the failure log captured in the deviation note.

## Self-Check: PASSED

- `src/sqlery/core/async_worker.py` — FOUND.
- `src/sqlery/async_worker.py` (stub) — FOUND.
- `tests/test_async_worker.py` — FOUND.
- `tests/test_async_worker_shutdown.py` — FOUND.
- Commits `2a75720`, `1e58f53`, `ac0488c`, `b8c942e`, `df77e70` — all present in `git log`.
- `pytest tests/test_async_worker.py tests/test_async_worker_shutdown.py` — 13 passed.
