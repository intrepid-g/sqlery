---
phase: 18-listen-notify
plan: "02"
subsystem: core/pg_notify + django_sqlery/backend + fastapi_sqlery/backend
tags: [pg-notify, listen-notify, enqueue-hook, security, phase18]
dependency_graph:
  requires: [18-01]
  provides: [pg_notify_enqueue_hook]
  affects: [DjangoBackend.create_job, SQLAlchemyBackend.create_job]
tech_stack:
  added: []
  patterns: [guard-import, on_commit hook, parameterized pg_notify]
key_files:
  created:
    - src/sqlery/core/pg_notify.py
    - tests/unit/test_pg_notify.py
  modified:
    - src/sqlery/django_sqlery/backend.py
    - src/sqlery/fastapi_sqlery/backend.py
decisions:
  - "notify_queue_django uses transaction.on_commit so NOTIFY fires only after INSERT commits"
  - "notify_queue_sqlalchemy executes inside the open session after session.commit() to stay in the same connection"
  - "Both notify helpers catch all exceptions so NOTIFY failure never breaks enqueue"
  - "Module-level guard-imports for Django/SQLAlchemy avoid hard coupling in core/"
  - "SQLERY_PG_NOTIFY flag check + vendor/dialect check are both in the backend, not just the helper, to skip even the function call overhead when off"
metrics:
  duration: ~8 min
  completed: 2026-06-12
  tasks_completed: 2
  files_created: 2
  files_modified: 2
---

# Phase 18 Plan 02: pg_notify Enqueue Hook Summary

Implemented opt-in PG LISTEN/NOTIFY enqueue hook for both Django and SQLAlchemy backends using a new `sanitize_queue_name_to_channel` + two thin `notify_queue_*` helpers.

## What Was Built

**`src/sqlery/core/pg_notify.py`** — New framework-agnostic module providing:
- `sanitize_queue_name_to_channel(queue_name)` — strips non-alphanumeric/underscore chars via `re.sub`, prepends `sqlery_job_`, truncates to 63 chars (PG identifier limit). Raises `ValueError` on empty input.
- `notify_queue_django(queue_name)` — schedules `SELECT pg_notify(%s, '')` via `transaction.on_commit`; no-op on SQLite or when Django unavailable.
- `notify_queue_sqlalchemy(queue_name, session)` — executes `SELECT pg_notify(:ch, '')` inside the already-open session after `session.commit()`; no-op on SQLite.
- Module-level guard-imports for `django.db.transaction` and `sqlalchemy.text` so the module loads cleanly in any environment.

**`src/sqlery/django_sqlery/backend.py`** — Added guard-import of `notify_queue_django`. In `DjangoBackend.create_job`, after the `QueuedJob.objects.create()` call on the main-queue path, calls `_notify_queue_django(queue_name)` when `get_setting('SQLERY_PG_NOTIFY', False)` is True and `connection.vendor == 'postgresql'`. Staging path (`ScheduledJob`) unchanged.

**`src/sqlery/fastapi_sqlery/backend.py`** — Added guard-import of `notify_queue_sqlalchemy`. In `SQLAlchemyBackend.create_job`, inside the `with self._get_session() as session:` block after `session.commit()` + `session.refresh(job)`, calls `_notify_queue_sqlalchemy(queue_name, session)` when `get_config('SQLERY_PG_NOTIFY', False)` is True and `get_engine().dialect.name == 'postgresql'`. The `return job` was moved to after the with-block. Staging path (`ScheduledJob`) unchanged.

**`tests/unit/test_pg_notify.py`** — 23 unit tests covering sanitize parametrize cases, truncation, empty raises, Django no-op paths (SQLite vendor, missing Django), on_commit scheduling for PG, `_fire_django_notify` SQL assembly and exception swallowing, SQLAlchemy no-op paths (sqlite dialect, missing _sa_text, missing bind), PG path SQL assembly and fallback to `session.bind`.

## Commits

| Hash | Task | Description |
|------|------|-------------|
| 79838c6 | Task 1 | feat(18-02): add pg_notify channel sanitizer and helpers |
| 69cdd67 | Task 2 | feat(18-02): wire pg_notify into both backend create_job paths |

## Deviations from Plan

None — plan executed exactly as written.

The pre-existing test failure `tests/test_models.py::TestScheduledTaskRecomputation::test_multiple_cron_changes` was confirmed to exist before these changes (verified via git stash check).

## Security

Threat T-18-02-01 (Injection) mitigated: `re.sub(r'[^a-zA-Z0-9_]', '_', queue_name)` ensures the channel name is a safe identifier before being passed as a bound parameter to `pg_notify(%s, '')` / `pg_notify(:ch, '')` — never raw-interpolated into SQL.

Threat T-18-02-04 (staging path emitting notify for unready jobs) mitigated: notify wired only to the main-queue path; the `ScheduledJob` staging branch returns before the notify block in both backends.

## Self-Check

Files created:
- src/sqlery/core/pg_notify.py — FOUND
- tests/unit/test_pg_notify.py — FOUND

Files modified:
- src/sqlery/django_sqlery/backend.py — FOUND
- src/sqlery/fastapi_sqlery/backend.py — FOUND

Commits:
- 79838c6 — FOUND
- 69cdd67 — FOUND

## Self-Check: PASSED
