---
phase: 01-core-unification
plan: 02
subsystem: core
tags: [unification, dead-code-marking, refactor]
requires: [DatabaseBackend ABC, sqlery.core.claiming, sqlery.core.worker]
provides:
  - canonical-import-path: sqlery.core.claiming
  - canonical-import-path: sqlery.core.worker
  - dated-stub: sqlery.django_sqlery.worker_claiming
  - dated-stub: sqlery.django_sqlery.executor
  - dated-stub: sqlery.executor
affects:
  - all in-repo callers of TaskExecutor / claim_next_job_with_queue_priority
tech-stack:
  added: []
  patterns:
    - "lazy module-level __getattr__ for cross-module re-exports"
    - "comment-and-date deprecation stubs (DEPRECATED YYYY-MM-DD … Remove after YYYY-MM-DD)"
key-files:
  created:
    - src/sqlery/django_sqlery/_executor_impl.py
  modified:
    - src/sqlery/core/claiming.py
    - src/sqlery/core/worker.py
    - src/sqlery/django_sqlery/worker_claiming.py
    - src/sqlery/django_sqlery/executor.py
    - src/sqlery/django_sqlery/backend.py
    - src/sqlery/django_sqlery/admin.py
    - src/sqlery/django_sqlery/daemon_worker.py
    - src/sqlery/django_sqlery/worker_process.py
    - src/sqlery/django_sqlery/worker_registry.py
    - src/sqlery/django_sqlery/management/commands/run_jobs.py
    - src/sqlery/django_sqlery/management/commands/rqworker.py
    - src/sqlery/django_sqlery/management/commands/run_scheduled_tasks.py
    - src/sqlery/triggers.py
    - src/sqlery/lambda_handler.py
    - src/sqlery/executor.py
    - src/sqlery/compat/scheduler.py
    - tests/chaos/test_worker_chaos.py
decisions:
  - "Use module-level __getattr__ to lazily resolve TaskExecutor: in Django mode it returns the historic Django-coupled class from sqlery.django_sqlery._executor_impl; in standalone mode it falls back to JobExecutor. This avoids importing Django at sqlery.core.worker load time."
  - "Move (not delete) the Django TaskExecutor body to a NEW internal module sqlery.django_sqlery._executor_impl. The class has 14 methods including scheduled-task helpers (get_due_tasks, _enqueue_for_scheduled_task, run_due_tasks, run_queue_workers, process_one_job, _spawn_next_worker, _cleanup_stale_jobs, _kill_worker_process) not present on the framework-agnostic JobExecutor; porting them into DatabaseBackend is deferred to a follow-up phase."
  - "Port the legacy Django release_job(worker, job, status, **kwargs) helper into sqlery.core.claiming with try/except guarded Django imports (mirrors the existing core/worker.py pattern). DatabaseBackend.release_job(job_id) remains the canonical framework-agnostic API."
  - "Removal date for all stubs: 2027-05-13 (12 months from creation, per CONTEXT.md open-question resolution)."
metrics:
  duration: 7m 28s
  completed: 2026-05-13
  tasks_completed: 3
  files_modified: 17
  files_created: 1
  commits: 3
---

# Phase 1 Plan 2: Retire Duplicate Claiming + Execution Modules Summary

Unified the duplicate claiming algorithm and job-executor implementations behind the canonical `sqlery.core.claiming` and `sqlery.core.worker` modules. Replaced the two duplicate source files with dated deprecation stubs and rewrote every in-repo caller to import from the canonical paths.

## What changed

### Task 1 — Expose canonical exports
- Added `TaskExecutor` lazy proxy (module `__getattr__`) to `sqlery.core.worker`, plus an `__all__` listing.
- Ported the legacy `release_job(worker, job, status, **kwargs)` helper into `sqlery.core.claiming` with try/except guarded Django imports. Added `__all__`.
- No behavioral change to existing functions.

### Task 2 — Update all in-repo callers
Rewrote imports in 12 files to use `from sqlery.core.claiming import …` and `from sqlery.core.worker import …` instead of the deprecated `django_sqlery.{worker_claiming,executor}` paths. Files touched: backend.py, admin.py, daemon_worker.py, worker_process.py, worker_registry.py, three management commands (run_jobs, rqworker, run_scheduled_tasks), top-level triggers.py and lambda_handler.py, compat/scheduler.py, and tests/chaos/test_worker_chaos.py.

Grep gate confirmed: zero live (non-comment) imports of the deprecated paths remain outside the stub files themselves.

### Task 3 — Convert deprecated modules to dated stubs
- `django_sqlery/worker_claiming.py`: 531 lines → 24-line stub re-exporting from `sqlery.core.claiming`.
- `django_sqlery/executor.py`: 685 lines → 43-line stub re-exporting from `sqlery.core.worker` + `_executor_impl`.
- `sqlery/executor.py`: small shim refreshed to the dated-stub pattern.
- NEW: `django_sqlery/_executor_impl.py` holds the verbatim Django-coupled `TaskExecutor` class (single source of truth for that class — not duplicated).

All three stubs carry the required deprecation header (`# DEPRECATED 2026-05-13 — moved to sqlery.core.{X}. Remove after 2027-05-13.`) and a defensive `__getattr__` fallback.

## Verification

- `python -c "from sqlery.core.worker import TaskExecutor, _current_job_var, JobExecutor; from sqlery.core.claiming import claim_next_job_with_queue_priority, release_job, get_node_id"` → OK.
- Identity check: `sqlery.django_sqlery.worker_claiming.claim_next_job_with_queue_priority is sqlery.core.claiming.claim_next_job_with_queue_priority` → True. Same for `release_job`, `get_node_id`, and `TaskExecutor`/`_current_job_var` via `sqlery.django_sqlery.executor` and `sqlery.executor`.
- Grep gate (`from {.,sqlery.django_sqlery.}{worker_claiming,executor} import`) returns zero hits outside the three stub files.
- Test suite (`PYTHONPATH=. uv run pytest tests/ -q --ignore=tests/chaos`): **358 passed, 25 failed, 9 skipped**. The 25 failures are pre-existing and identical to the baseline captured immediately before the edits (verified by `git stash` round-trip) — they relate to `django_tasks` integration and subprocess middleware, none of which touch claiming or execution.

## Success criteria

- UNIF-01 satisfied — `django_sqlery/worker_claiming.py` delegates to `sqlery.core.claiming`.
- UNIF-02 satisfied — `django_sqlery/executor.py` delegates to `sqlery.core.worker` (with the historic Django TaskExecutor relocated to `_executor_impl.py`).
- No in-repo caller imports from the deprecated paths.
- Dead-code policy honored: files retained as dated stubs, not deleted.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 — Missing critical functionality] TaskExecutor cannot be a plain alias to JobExecutor**
- **Found during:** Task 1 verification (regression run after the trivial `TaskExecutor = JobExecutor` alias broke `tests/test_admin.py::TestScheduledTaskAdmin::test_enqueue_now_action` with `AttributeError: 'JobExecutor' object has no attribute '_enqueue_for_scheduled_task'`).
- **Issue:** The plan's `<interfaces>` block stated `TaskExecutor` is "likely aliased as `TaskExecutor` for backward compat — verify and re-alias if needed". In reality the historic Django `TaskExecutor` has 14 methods (including scheduled-task orchestration: `get_due_tasks`, `_enqueue_for_scheduled_task`, `run_due_tasks`, `run_queue_workers`, `process_one_job`, `_spawn_next_worker`, `_cleanup_stale_jobs`, `_kill_worker_process`) while the framework-agnostic `JobExecutor` only has 8 methods. They are not interchangeable.
- **Fix:** Implemented a module-level `__getattr__` in `sqlery.core.worker` that lazily resolves `TaskExecutor` to the historic Django-coupled class when Django is installed (via `sqlery.django_sqlery._executor_impl`), falling back to `JobExecutor` when Django is absent. Preserves the single-import-path goal of the plan without losing the Django-specific functionality.
- **Files modified:** src/sqlery/core/worker.py
- **Commit:** 58e041b

**2. [Rule 3 — Blocking issue] release_job(worker, job, status, **kwargs) helper had no canonical home**
- **Found during:** Task 1 read-first scan.
- **Issue:** `sqlery.core.claiming` exports a `release_job` symbol referenced by the plan's `<interfaces>` block, but the function did NOT exist in core/claiming.py. The Django version (`django_sqlery/worker_claiming.py:464`) is used by `django_sqlery/worker_process.py` with a Django-coupled signature that's incompatible with `DatabaseBackend.release_job(job_id)`.
- **Fix:** Ported the function verbatim into `sqlery.core.claiming` with try/except guarded Django imports (same pattern `sqlery.core.worker` already uses for `django.db.connections`). The function raises a clear `RuntimeError` if called without Django installed, with a pointer to `DatabaseBackend.release_job(job_id)`.
- **Files modified:** src/sqlery/core/claiming.py
- **Commit:** 3d89a3e

**3. [Rule 3 — Blocking issue] Plan-required <30-line line count for executor stub conflicted with no-deletion policy**
- **Found during:** Task 3 read-first scan.
- **Issue:** The plan instructs to reduce `django_sqlery/executor.py` to a <30-line stub, but the historic Django `TaskExecutor` class body (~600 lines) has no canonical home — the framework-agnostic `JobExecutor` cannot host its Django ORM-coupled scheduled-task methods, and Rule 4 (architectural ABC extension) was explicitly out of scope per CONTEXT.md's `<deferred>` section.
- **Fix:** Moved (not duplicated) the historic class body to a new internal module `sqlery.django_sqlery._executor_impl`. Single source of truth preserved. The dated stub at `django_sqlery/executor.py` now re-exports `TaskExecutor` from there. This honors both the dead-code policy and the no-duplication principle.
- **Files created:** src/sqlery/django_sqlery/_executor_impl.py
- **Commit:** c09e630

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Lazy `__getattr__` proxy for `TaskExecutor` | Avoid importing Django at `sqlery.core.worker` module load while preserving the historic public name (`TaskExecutor`) callers expect. |
| Relocate (not delete) the Django `TaskExecutor` body to `_executor_impl.py` | Single source of truth; honors the dead-code policy; keeps the impl out of `core/` while still making it accessible through the canonical import path. |
| Guard `release_job(worker, job, …)` Django imports with try/except in core | Mirrors the existing pattern in `core/worker.py`; preserves the Django runner without coupling core to Django at import time. |
| Removal date `2027-05-13` for all three stubs | 12 months from creation, per CONTEXT.md open-question resolution. |

## Authentication Gates

None — pure refactor, no external services involved.

## Known Stubs

None introduced by this plan. The three dated stub files created (`worker_claiming.py`, `executor.py`, top-level `executor.py`) are the intentional dead-code markers required by the project policy and the plan's success criteria — they are NOT placeholder/no-data stubs.

## Self-Check: PASSED

- ✅ src/sqlery/core/claiming.py — modified (verified `release_job` present)
- ✅ src/sqlery/core/worker.py — modified (verified `__getattr__` resolves `TaskExecutor`)
- ✅ src/sqlery/django_sqlery/worker_claiming.py — dated stub (24 lines)
- ✅ src/sqlery/django_sqlery/executor.py — dated stub (43 lines)
- ✅ src/sqlery/django_sqlery/_executor_impl.py — new file, holds Django TaskExecutor
- ✅ src/sqlery/executor.py — dated stub
- ✅ All 12 caller files rewritten to canonical paths
- ✅ Commits 3d89a3e, 58e041b, c09e630 present in `git log d2d5aac..HEAD`
- ✅ Regression: 358 pass / 25 fail = identical to pre-edit baseline
