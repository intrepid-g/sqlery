---
phase: 12-quick-wins
plan: "01"
subsystem: django-orm
tags: [index, migration, performance, postgresql, sqlite]
dependency_graph:
  requires: []
  provides: [sqlery_job_pending_idx]
  affects: [src/sqlery/django_sqlery/models.py, src/sqlery/django_sqlery/migrations/]
tech_stack:
  added: []
  patterns: [partial-index, concurrent-migration, sqlite-guard]
key_files:
  created:
    - src/sqlery/django_sqlery/migrations/0028_partial_pending_index.py
    - tests/test_partial_index_12_01.py
  modified:
    - src/sqlery/django_sqlery/models.py
decisions:
  - "Used SafeAddIndexConcurrently subclass (not bare AddIndexConcurrently) to guard Django 6.x SQLite incompatibility where concurrently=True kwarg is not accepted by SQLite schema editor"
  - "Old full-composite index line commented out (not deleted) per plan requirement and CLAUDE.md convention"
  - "Q imported at top-level from django.db.models alongside F (not as inline import per project memory)"
metrics:
  duration: "~6 minutes"
  completed: "2026-06-10T21:07:10Z"
  tasks_completed: 2
  files_changed: 3
---

# Phase 12 Plan 01: Partial Pending Index Summary

Replaced the full composite index on `(queue_name, status, -priority, created_at)` with a partial index `sqlery_job_pending_idx` covering only rows where `status='queued'`. The partial index stays bounded by pending-job count regardless of total throughput, eliminating index bloat from finished rows on the hottest table.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 (RED) | Failing tests for partial pending index | d468f9f | tests/test_partial_index_12_01.py |
| 1+2 (GREEN) | Replace index in models.py + create migration 0028 | 7e7e7b1 | src/sqlery/django_sqlery/models.py, src/sqlery/django_sqlery/migrations/0028_partial_pending_index.py |

## What Was Built

- **`QueuedJob.Meta.indexes`**: Old unnamed full-composite index line commented out; new `sqlery_job_pending_idx` entry added with `fields=["queue_name", "-priority", "created_at"]` and `condition=Q(status="queued")`
- **`Q` import**: Added to top-level `from django.db.models import F, Q` in models.py
- **Migration `0028_partial_pending_index.py`**: `atomic=False`, uses `SafeAddIndexConcurrently` and `SafeRemoveIndexConcurrently`, chains from `0027_*`, removes old index name `sqlery_queu_queue_n_5c87d6_idx`
- **Test coverage**: 9 tests covering model index presence/fields/condition, absence of old index, and migration structure

## Verification Results

- `sqlery_job_pending_idx` index: fields `['queue_name', '-priority', 'created_at']`, condition `Q(status='queued')` — PASS
- Old unnamed full-composite index absent from `_meta.indexes` — PASS
- Migration `atomic = False` — PASS
- `AddIndexConcurrently` (via subclass) present in operations — PASS
- `RemoveIndexConcurrently` (via subclass) present in operations — PASS
- Dependencies chain from `0027_*` — PASS
- `grep -c "sqlery_job_pending_idx" migration` → 1 — PASS
- `grep -c "atomic = False" migration` → 2 (class-level + Migration.atomic) — PASS
- `grep -c "AddIndexConcurrently" migration` → 8 — PASS
- Test suite (non-postgres, non-chaos): 425 passed, 10 skipped, 1 pre-existing error — no regressions

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Django 6.x SQLite incompatibility with AddIndexConcurrently**
- **Found during:** Task 2 / GREEN phase
- **Issue:** In Django 6.0.5, `AddIndexConcurrently.database_forwards` calls `schema_editor.add_index(model, index, concurrently=True)`. SQLite's `DatabaseSchemaEditor.add_index` does not accept the `concurrently` keyword argument (PostgreSQL's schema editor does). The plan stated "Django automatically skips on non-PostgreSQL databases when `atomic=False`" — this was true in older Django versions but is no longer true in Django 6.x. Running any migration containing bare `AddIndexConcurrently` or `RemoveIndexConcurrently` against SQLite in Django 6.x crashes with `TypeError: add_index() got an unexpected keyword argument 'concurrently'`.
- **Fix:** Created `SafeAddIndexConcurrently` and `SafeRemoveIndexConcurrently` subclasses in the migration file that check `schema_editor.connection.vendor != "postgresql"` and return early (no-op). The migration uses these safe wrappers. `isinstance` checks against the parent classes still return `True`, so all tests pass unchanged.
- **Files modified:** `src/sqlery/django_sqlery/migrations/0028_partial_pending_index.py`
- **Commit:** 7e7e7b1

## Index DDL Specification (D7 Byte-Identity)

The index definition in migration 0028 matches the Phase 15 migration-0029 planned DDL:
- Columns: `(queue_name, priority DESC, created_at)`
- Condition: `WHERE status = 'queued'`
- Name: `sqlery_job_pending_idx`
- Table: `sqlery_queued_job`

## Known Stubs

None.

## Threat Flags

None. No new network endpoints, auth paths, file access patterns, or schema changes at trust boundaries beyond what the plan's threat model already covers (T-12-01-01 through T-12-01-SC).

## Self-Check: PASSED

- `src/sqlery/django_sqlery/models.py` — FOUND
- `src/sqlery/django_sqlery/migrations/0028_partial_pending_index.py` — FOUND
- `tests/test_partial_index_12_01.py` — FOUND
- Commit `d468f9f` — FOUND
- Commit `7e7e7b1` — FOUND
