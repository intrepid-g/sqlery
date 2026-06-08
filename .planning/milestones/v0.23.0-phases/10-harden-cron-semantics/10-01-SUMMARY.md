---
phase: 10-harden-cron-semantics
plan: 01
subsystem: scheduler-backend
tags: [cron, atomicity, cas, idempotency, backend-abc]
requires:
  - "DatabaseBackend ABC (src/sqlery/compat/__init__.py)"
  - "SQLAlchemyBackend.create_job field mapping (src/sqlery/fastapi_sqlery/backend.py)"
  - "DjangoBackend.create_job (src/sqlery/django_sqlery/backend.py)"
provides:
  - "advance_scheduled_task_if_due abstractmethod on DatabaseBackend"
  - "SQLAlchemyBackend.advance_scheduled_task_if_due (Postgres lock + SQLite predicate-CAS)"
  - "DjangoBackend.advance_scheduled_task_if_due (transaction.atomic + .update() rowcount-CAS)"
  - "SQLAlchemyBackend._build_queued_job (in-session QueuedJob constructor)"
affects:
  - "src/sqlery/core/scheduler.py (rewired to call this primitive in Plan 03)"
tech-stack:
  added: []
  patterns:
    - "CAS on observed next_run_at (no version column on ScheduledTask)"
    - "Single-transaction advance+enqueue (CRON-01)"
    - "Dialect split: Postgres with_for_update() blocking lock vs SQLite predicate-CAS"
key-files:
  created: []
  modified:
    - "src/sqlery/compat/__init__.py"
    - "src/sqlery/fastapi_sqlery/backend.py"
    - "src/sqlery/django_sqlery/backend.py"
decisions:
  - "Used inline QueuedJob construction (_build_queued_job) in the standalone in-session enqueue instead of refactoring create_job to accept a session, keeping create_job untouched"
  - "Postgres standalone branch uses blocking with_for_update() (not skip_locked) for the single-key ScheduledTask row, per CR-01 rationale"
  - "Django uses .update() rowcount-CAS only (no select_for_update) — rowcount-CAS already gives exactly-once on both engines"
metrics:
  duration: ~12m
  completed: 2026-06-08
  tasks: 3
  files: 3
---

# Phase 10 Plan 01: Atomic advance_scheduled_task_if_due Primitive Summary

Added one atomic backend primitive — `advance_scheduled_task_if_due` — that folds CRON-01 (atomic enqueue + next_run_at advance in one transaction) and CRON-04 (exactly-once under two-leader overlap) into a single compare-and-swap on the observed `next_run_at`, declared on the ABC and implemented identically in both backends.

## What Was Built

- **ABC declaration** (`src/sqlery/compat/__init__.py:540`): `@abstractmethod advance_scheduled_task_if_due(self, task_id, observed_next_run_at, new_next_run_at, job_kwargs) -> Any`. Returns the created job when this caller wins the CAS, else `None`. Placed immediately after `update_scheduled_task_next_run`. Abstract (no default body) so both backends are forced to implement it.

- **Standalone implementation** (`src/sqlery/fastapi_sqlery/backend.py`, method at the former line ~991, near the scheduled-task methods; helper `_build_queued_job` follows it):
  - Opens ONE session via `self._get_session()`; dispatches on `determine_claim_strategy(session.bind.dialect.name)`.
  - Postgres (`skip_locked` strategy): `select(ScheduledTask).where(id==task_id).with_for_update()` blocking lock, naive->aware normalization of both `existing.next_run_at` and `observed_next_run_at`, compare; on match set `next_run_at`, add the new QueuedJob in the same session, commit, return job; on mismatch return `None`.
  - SQLite/fallback: `update(ScheduledTask).where(id==task_id).where(next_run_at==observed_next_run_at).values(next_run_at=new_next_run_at).execution_options(synchronize_session=False)`; `res.rowcount == 1` -> add QueuedJob, commit, return job; else rollback and return `None`.

- **Django implementation** (`src/sqlery/django_sqlery/backend.py:686`): `@retry_on_db_error()` decorated; `with transaction.atomic():` wraps `advanced = ScheduledTask.objects.filter(id=task_id, next_run_at=observed_next_run_at).update(next_run_at=new_next_run_at)`; if `advanced != 1` return `None`; else `return self.create_job(**job_kwargs)` in the same atomic block.

## Final ABC Signature

```python
@abstractmethod
def advance_scheduled_task_if_due(
    self,
    task_id: int,
    observed_next_run_at: datetime,
    new_next_run_at: datetime,
    job_kwargs: dict,
) -> Any:
```

## create_job Field Mapping (standalone in-session enqueue)

`_build_queued_job(job_kwargs)` constructs `QueuedJob` mirroring `create_job`'s field mapping, reading from `job_kwargs`:

| QueuedJob field | Source | Default |
|---|---|---|
| task_path | `job_kwargs["task_path"]` | required |
| kwargs | `job_kwargs.get("kwargs")` | `{}` |
| queue_name | `job_kwargs["queue_name"]` | required |
| priority | `job_kwargs.get("priority")` | `0` |
| scheduled_at | `job_kwargs.get("scheduled_at")` | `None` |
| max_retries | `job_kwargs.get("max_retries")` | `0` |
| retry_backoff | `job_kwargs.get("retry_backoff")` | `0.0` |
| allow_parallel | `job_kwargs.get("allow_parallel")` | `False` |
| timeout_seconds | `job_kwargs.get("timeout_seconds")` | `None` |
| retry_count | `job_kwargs["retry_count"]` if not None | `0` |
| scheduled_task_id | `job_kwargs.get("scheduled_task_id")` | `None` |
| job_name | `job_kwargs.get("job_name")` | `None` |
| retry_intervals | `job_kwargs.get("retry_intervals")` | `None` |
| meta | `job_kwargs.get("meta")` | `None` |
| dependencies | `job_kwargs.get("dependencies")` | `[]` |
| on_success_path | `job_kwargs.get("on_success_path")` | `""` |
| on_failure_path | `job_kwargs.get("on_failure_path")` | `""` |
| ttl / result_ttl / failure_ttl | `job_kwargs.get(...)` | `None` |
| parent_job_id | `job_kwargs.get("parent_job_id")` | `None` |
| status | constant | `"queued"` |

Note: `_build_queued_job` does NOT replicate `create_job`'s named-job dedup pass (the `job_name` conflict-deletion); scheduled enqueues do not pass `job_name`, so this is intentionally omitted to keep the in-session path minimal.

## Naive/Aware Normalization Applied

Standalone Postgres branch only — SQLite returns naive datetimes. Both the stored `existing.next_run_at` and the passed `observed_next_run_at` are normalized to UTC-aware before the equality compare:

```python
existing_due = existing.next_run_at if existing.next_run_at.tzinfo else existing.next_run_at.replace(tzinfo=UTC)
observed = observed_next_run_at if observed_next_run_at.tzinfo else observed_next_run_at.replace(tzinfo=UTC)
```

The SQLite branch needs no Python-side normalization because `synchronize_session=False` pushes the comparison into the database (raw SQL), skipping the ORM evaluator.

## Verification

- ABC: `advance_scheduled_task_if_due.__isabstractmethod__ is True`; param order exactly `(self, task_id, observed_next_run_at, new_next_run_at, job_kwargs)` — prints `ABC OK`.
- `SQLAlchemyBackend()` instantiable (no remaining abstractmethods) — prints `STANDALONE IMPL OK + INSTANTIABLE`.
- `DjangoBackend()` instantiable under `DJANGO_SETTINGS_MODULE=tests.settings` — prints `DJANGO IMPL OK + INSTANTIABLE`.
- grep confirms: standalone `with_for_update()`, `synchronize_session=False`, `rowcount == 1` inside the method; Django `transaction.atomic`, `next_run_at=observed_next_run_at`, `self.create_job(`.
- `black --check` passes on all three touched files.
- No new inline imports; no new third-party dependency (CLAUDE.md "no new deps" satisfied).
- Interval/once helpers (`has_pending_job_for_scheduled_task`, `update_scheduled_task`, `update_scheduled_task_next_run`) left unchanged for Plan 03.

## Deviations from Plan

None - plan executed exactly as written. The standalone in-session enqueue used the planner-preferred option (inline `QueuedJob` construction via a small `_build_queued_job` helper rather than refactoring `create_job` to accept a session).

## Notes for Plan 03 / Plan 04

- Behavioral proof of exactly-once under concurrency (two callers, same `observed_next_run_at`) is explicitly deferred to Plan 04 per each task's `<done>` note. The TDD-tagged tasks here delivered structural/instantiability verification; the concurrency behavior tests land in Plan 04.
- Plan 03 wires `core/scheduler.py` to call this primitive: capture `observed_due = task.next_run_at` from the `run_due_tasks` row read, compute `new_next_run`, build `job_kwargs` mirroring the current `_enqueue_for_scheduled_task` create_job call site, and replace the check-then-act sequence.

## Self-Check: PASSED
- FOUND: src/sqlery/compat/__init__.py (advance_scheduled_task_if_due abstractmethod)
- FOUND: src/sqlery/fastapi_sqlery/backend.py (advance_scheduled_task_if_due + _build_queued_job)
- FOUND: src/sqlery/django_sqlery/backend.py (advance_scheduled_task_if_due)
- FOUND commit cc3712e (Task 1), 61bcee9 (Task 2), 3c41768 (Task 3)
