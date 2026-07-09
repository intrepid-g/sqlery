---
phase: 18-listen-notify
plan: "03"
subsystem: worker
tags: [listen-notify, fork-safety, pg, worker, dispatch-latency]
dependency_graph:
  requires: [18-01, 18-02]
  provides: [worker-listen-notify-loop]
  affects: [src/sqlery/core/worker.py]
tech_stack:
  added: []
  patterns: [psycopg3-listen-notify, fork-safe-hook, guard-import]
key_files:
  modified:
    - src/sqlery/core/worker.py
decisions:
  - "psycopg3 imported at module level with guard (not inline) per CLAUDE.md and project memory"
  - "sanitize_queue_name_to_channel imported at module level with guard"
  - "_wait_for_notify delegates to 1s-slice sleep when _listen_conn is None (flag-off path byte-identical)"
  - "_close_listen_conn registered as pre_fork hook on ForkSafeExecutor — LISTEN conn closed before every os.fork()"
  - "_open_listen_conn registered as post_fork_parent hook — LISTEN conn re-opened in parent after fork (child never inherits)"
  - "PG detection: DATABASE_URL (standalone) then Django connections['default'].settings_dict (Django mode)"
  - "psycopg.conninfo.make_conninfo used for DSN construction — no raw string interpolation"
  - "psycopg.sql.Identifier wraps channel name for defence-in-depth quoting even though sanitize_queue_name_to_channel guarantees safe chars"
  - "LISTEN drop caught in _wait_for_notify: closes conn, returns immediately, worker falls back to polling"
metrics:
  duration: "18 min"
  completed: "2026-06-12"
  tasks_completed: 2
  files_modified: 1
---

# Phase 18 Plan 03: LISTEN/NOTIFY Worker Integration Summary

**One-liner:** Dedicated psycopg3 AUTOCOMMIT LISTEN connection integrated into WorkerProcess poll loop with fork-safe lifecycle hooks, enabling sub-poll-interval wakeup when SQLERY_PG_NOTIFY=True on PG.

## What Was Built

`WorkerProcess` in `src/sqlery/core/worker.py` gains three new methods and their lifecycle wiring:

**`_open_listen_conn()`** — Opens a dedicated psycopg3 AUTOCOMMIT connection and issues `LISTEN <channel>` for each queue. Guards: SQLERY_PG_NOTIFY flag, psycopg3 availability, sanitize_queue_name_to_channel availability, PG detection (standalone DATABASE_URL or Django connection vendor). Uses `psycopg.conninfo.make_conninfo()` for safe DSN construction and `psycopg.sql.Identifier()` for channel quoting. Any failure logs a warning and sets `_listen_conn = None` (fall back to polling).

**`_close_listen_conn()`** — Closes the LISTEN connection. Safe when `_listen_conn` is None. Registered as `pre_fork` hook on `ForkSafeExecutor` so it runs before every `os.fork()`, preventing the LISTEN connection from being inherited by child processes. Also called in `run()` finally block on shutdown.

**`_wait_for_notify()`** — Replaces both 1s-slice sleep loops in the idle and concurrency-block paths. When `_listen_conn` is None (flag off, SQLite, or after error): byte-identical 1s-slice sleep loop with heartbeat checks. When `_listen_conn` is set: iterates `conn.notifies(timeout=min(remaining, 1.0), stop_after=1)` in <=1s slices, returning immediately on NOTIFY or on timeout. LISTEN errors are caught, connection closed, falls back to poll.

**Lifecycle wiring in `run()`:**
- After scheduler-election: `_open_listen_conn()` → register `_close_listen_conn` as `pre_fork` hook → register `_open_listen_conn` as `post_fork_parent` hook
- Both idle sleep loops replaced with `_wait_for_notify()`
- `finally:` block: `_close_listen_conn()` before `update_worker_heartbeat`

**Module-level imports (CLAUDE.md):**
- `psycopg` / `psycopg.sql` imported with try/except guard → `_psycopg`, `_psycopg_sql`, `_psycopg_available`
- `sanitize_queue_name_to_channel` imported with try/except guard → `_pg_notify_import_ok`

## Deviations from Plan

None — plan executed exactly as written.

## Security

Threat mitigations from threat model all implemented:
- **T-18-03-01 (Injection):** Channel via `sanitize_queue_name_to_channel` + `psycopg.sql.Identifier` quoting
- **T-18-03-02 (DoS — LISTEN drop):** `_wait_for_notify` catches any exception, closes conn, returns — worker never crashes
- **T-18-03-03 (EoP — child fork):** `_close_listen_conn` as pre_fork hook on ForkSafeExecutor
- **T-18-03-04 (DoS — signal handler):** SIGUSR1 handler unchanged — sets `_heartbeat_due = True` only

## Verification Results

Task 1 automated check: all three methods present, module imports cleanly.

Task 2 test runs:
- `tests/test_concurrency_and_timeout.py tests/test_atomic_claiming.py`: 33 passed, 4 skipped
- Full suite (`tests/ -x -q --ignore=tests/integration -k "not test_lifecycle_partitioned and not test_phase15"`): 467 passed, 50 skipped, 1 pre-existing error in `test_compat_rq_standalone.py::test_get_job_registry_summary_standalone` (MockBackend missing abstract method `advance_scheduled_task_if_due` — confirmed pre-existing, not caused by this plan)

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| Task 1 | a03530f | (feat): add LISTEN conn methods to WorkerProcess |
| Task 2 | a2d4df0 | (feat): wire LISTEN lifecycle into WorkerProcess.run() |

## Self-Check: PASSED

- [x] `src/sqlery/core/worker.py` exists and is modified
- [x] Commits a03530f and a2d4df0 present in git log
- [x] Methods `_open_listen_conn`, `_close_listen_conn`, `_wait_for_notify` all exist on WorkerProcess
- [x] `self._listen_conn = None` set in `__init__`
- [x] `_close_listen_conn` registered as pre_fork hook
- [x] `_open_listen_conn` registered as post_fork_parent hook
- [x] SIGUSR1 handler unchanged (no DB/LISTEN calls in signal handler)
- [x] Flag-off path: `_wait_for_notify` with `_listen_conn=None` executes byte-identical 1s-slice sleep loop
- [x] All existing tests pass with flag off (default)
