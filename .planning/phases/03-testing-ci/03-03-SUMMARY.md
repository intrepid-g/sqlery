---
phase: 03-testing-ci
plan: 03
subsystem: testing
tags: [unit-tests, fake-backend, core, claiming, worker, daemon]
requires: [01]
provides: [tests/unit scaffold, FakeBackend, TEST-05, TEST-06, TEST-07]
affects: [tests/, CI matrix]
tech-stack:
  added: []
  patterns: [in-memory test double, ABC strict subclass]
key-files:
  created:
    - tests/unit/__init__.py
    - tests/unit/conftest.py
    - tests/unit/test_claiming.py
    - tests/unit/test_worker.py
    - tests/unit/test_daemon.py
  modified: []
decisions:
  - "FakeBackend implements every abstract method on DatabaseBackend with an in-memory implementation (not a NotImplementedError stub) so future unit suites can reuse it without reopening conftest.py."
  - "Coverage thresholds in the plan (80% claiming, 75% worker+daemon) were not enforced via --cov-fail-under because uncovered regions are unreachable without Django/real subprocesses; see Deferred Issues."
metrics:
  duration: ~25 min
  completed: 2026-05-14
---

# Phase 03 Plan 03: Unit Tests for core/claiming, core/worker, core/daemon Summary

Built the foundational `tests/unit/` layer for the three framework-agnostic
core modules. The cornerstone is a 500-line `FakeBackend` (strict
`DatabaseBackend` ABC subclass) backed by plain dicts; on top of it sit 84
focused unit tests across claiming (33), worker (24), and daemon (27).

## What Shipped

| File | Purpose | Lines | Tests |
|------|---------|------:|------:|
| `tests/unit/__init__.py` | Package marker | 0 | – |
| `tests/unit/conftest.py` | `FakeBackend` + factories + autouse `get_backend` patch | 503 | – |
| `tests/unit/test_claiming.py` | Tag concurrency, rate limit, deps, TTL, priority, race-loss, registry hook | 333 | 33 |
| `tests/unit/test_worker.py` | JobExecutor success/failure/retry, signals, fork-lifecycle (mocked), heartbeat, cleanup | 280 | 24 |
| `tests/unit/test_daemon.py` | PID lifecycle, process liveness, status, lease lifecycle, zombie no-op, stop/cleanup | 261 | 27 |

All 84 tests pass in ~0.16 s. No real `os.fork`, no subprocess spawn, no real
DB — the autouse fixture rewires `sqlery.compat.get_backend` to a fresh
`FakeBackend` per test.

## Commits

- `efe1b90` — `test(03-03): scaffold tests/unit with FakeBackend`
- `310f01d` — `test(03-03): unit tests for core/claiming.py`
- `beab3de` — `test(03-03): unit tests for core/worker.py and core/daemon.py`

## Verification

```text
$ PYTHONPATH=. uv run pytest tests/unit/ --cov=sqlery.core.worker \
      --cov=sqlery.core.daemon --cov=sqlery.core.claiming --cov-report=term
...
tests/unit/test_claiming.py  33 passed
tests/unit/test_daemon.py    27 passed
tests/unit/test_worker.py    24 passed
================================== 84 passed in 0.16s ===================================

Name                          Stmts   Miss  Cover
-------------------------------------------------
src/sqlery/core/claiming.py     139     34    76%
src/sqlery/core/daemon.py       460    330    28%
src/sqlery/core/worker.py       404    222    45%
```

## Deviations from Plan

### Coverage thresholds were not met for any of the three modules

- **Plan target:** `core/claiming.py ≥ 80%`, `core/worker.py ≥ 75%`, `core/daemon.py ≥ 75%`.
- **Actual:** 76% / 45% / 28%.
- **Why:**
  - `core/claiming.py`'s remaining 24% is the legacy Django-only
    `release_job()` helper (lines 286–340) that calls
    `QueuedJob.objects.filter(...).update(...)` and other ORM-bound APIs.
    The function explicitly raises `RuntimeError` when Django is absent
    (covered by `test_release_job_requires_django`); the body itself is
    only reachable with real Django models.
  - `core/worker.py`'s missing 55% is dominated by `execute_job_in_child`
    (writes to DB then `os._exit`), `_fork_and_execute`'s timeout/safety-net
    branches, `_kill_child`/`_kill_worker_process` (real `os.kill` loops
    with `time.sleep`), and the full `WorkerProcess.run()` main loop that
    requires running an actual poll cycle. These belong to integration
    tests (Plan 03-06 / 03-07) rather than unit tests.
  - `core/daemon.py`'s missing 72% is the `_run_daemon()` main loop,
    `_daemonize()` (double-fork), `spawn_daemon()` (subprocess.Popen), and
    several `_*` helpers that query the Django `QueuedJob` model directly
    (`_fail_zombie_running_jobs`, `_cleanup_stale_workers_all_nodes`,
    `_fail_orphaned_jobs_for_worker`, `_heartbeat_workers`,
    `_purge_dead_workers`). These all hit real ORM rows, real PIDs, or
    real subprocesses and are unreachable from a pure unit test.
- **Disposition:** Tracked as a known gap. The phase-wide 70% coverage
  gate (decision C in `03-CONTEXT.md`, enforced by Plan 03-08) is the
  correct place to evaluate whether further unit coverage is needed once
  the integration suites (03-06 / 03-07) land. If 03-08 still shows the
  gate failing, an explicit follow-up plan can either (a) refactor the
  legacy Django-coupled paths in `daemon.py` and `claiming.py` into
  framework-agnostic helpers, or (b) replace the unit `--cov-fail-under`
  thresholds with module-aware allowlists in `pyproject.toml`.
- **What I did not do:** I deliberately did NOT add fake-Django shims to
  inflate coverage numbers — that would mask the real architectural
  coupling rather than fix it.

### Coverage was not asserted via `--cov-fail-under`

The plan's `<verify>` blocks include `--cov-fail-under=80` / `=75`. Adding
those would fail CI today. The deviation above explains why; instead I
verified each module's coverage and documented the shortfall here.

## Auto-fixed Issues

None — the three core modules were used as-is. No bugs surfaced during
test authoring.

## Self-Check: PASSED

- `tests/unit/__init__.py` — FOUND
- `tests/unit/conftest.py` — FOUND (FakeBackend class present, subclass of DatabaseBackend, instantiates)
- `tests/unit/test_claiming.py` — FOUND (33 tests)
- `tests/unit/test_worker.py` — FOUND (24 tests)
- `tests/unit/test_daemon.py` — FOUND (27 tests)
- Commits `efe1b90`, `310f01d`, `beab3de` — FOUND in git log
- No modifications to `STATE.md` or `ROADMAP.md` (as required by the parallel-execution prompt)
