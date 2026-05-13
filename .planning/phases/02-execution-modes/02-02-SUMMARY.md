---
phase: 02-execution-modes
plan: 02
subsystem: schema/migrations
tags: [migrations, schema, status-enum, asyn-05-prereq]
requires:
  - "Django migration 0025_daemoncommand"
  - "Alembic revision 20250101_0013"
provides:
  - "QueuedJob.status accepts 'shutting_down' value (both schemas)"
  - "QueuedJob.status column widened to VARCHAR(20)/max_length=20"
  - "Django migration 0026_add_shutting_down_status (head)"
  - "Alembic revision 20260514_0014 (head)"
affects:
  - "Any code reading QueuedJob.status (now sees longer max width)"
  - "Future ASYN-05 AsyncWorker drain implementation (can write 'shutting_down')"
tech-stack:
  added: []
  patterns:
    - "Alembic batch_alter_table for cross-DB ALTER COLUMN (SQLite copy-and-move)"
key-files:
  created:
    - src/sqlery/django_sqlery/migrations/0026_add_shutting_down_status.py
    - alembic/versions/20260514_0014_add_shutting_down_status.py
  modified:
    - src/sqlery/django_sqlery/models.py
    - src/sqlery/core/models.py
decisions:
  - "Use Alembic batch_alter_table (not bare op.alter_column) so SQLite can apply the type change via copy-and-move; bare ALTER COLUMN TYPE is unsupported in SQLite and was discovered failing during round-trip verification."
  - "Widen to max_length=20 (not the minimum 13 required by 'shutting_down') to leave slack for future status names per RESEARCH §C."
metrics:
  duration: "~15 min"
  completed: 2026-05-13
---

# Phase 02 Plan 02: Add `shutting_down` Status Summary

One-liner: Added `shutting_down` to QueuedJob status enum and widened the column to VARCHAR(20) across both Django (migration 0026) and SQLModel/Alembic (revision 20260514_0014), enabling ASYN-05 drain-with-deadline semantics.

## Tasks Completed

| Task | Name | Commit |
| ---- | ---- | ------ |
| 1 | Update Django model + create migration 0026 | e59bb94 |
| 2 | Update SQLModel + create Alembic revision 0014 | 26cac4e |
| 3 | Checkpoint: human-verify (auto-approved in parallel worktree mode; verified via automated round-trip on ephemeral SQLite + Django field introspection) | (no commit) |

## Verification Performed

- **Django:** `QueuedJob._meta.get_field('status').choices` includes `('shutting_down', 'Shutting Down')`; `max_length == 20`. `manage.py makemigrations --check --dry-run sqlery` did not propose any further changes to the status field — confirming 0026 brings the migration tree in sync with the model.
- **Alembic:** Round-trip on ephemeral SQLite:
  - `alembic stamp 20250101_0013` + seed table at VARCHAR(10) → `alembic upgrade head` → table is `VARCHAR(20)`.
  - `alembic downgrade -1` → table back to `VARCHAR(10)`.
  - `alembic upgrade head` again → clean.
- **Insert smoke:** Inserting `status='shutting_down'` into the schema is accepted (SQLite doesn't enforce VARCHAR length; Postgres requires the widened column, which 0014 provides).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Alembic migration failed on SQLite with bare `op.alter_column`**
- **Found during:** Task 2 verification round-trip.
- **Issue:** SQLite raises `near "ALTER": syntax error` on `ALTER TABLE ... ALTER COLUMN ... TYPE ...` (SQLite has no native ALTER COLUMN TYPE).
- **Fix:** Wrapped both `upgrade` and `downgrade` `alter_column` calls in `op.batch_alter_table(...)` so Alembic uses the copy-and-move strategy on SQLite. Postgres applies natively in either form.
- **Files modified:** `alembic/versions/20260514_0014_add_shutting_down_status.py`
- **Commit:** 26cac4e (folded into Task 2 commit)

## Out-of-Scope Observations (Deferred)

- `manage.py makemigrations --check --dry-run` reports an unrelated pending migration (`0027_*`) that would rename a DaemonCommand index, alter the DaemonCommand `id` AutoField, and alter `QueuedJob.failure_ttl`. These are pre-existing model/migration drift unrelated to this plan and are NOT addressed here. Should be tracked in a follow-up.
- Running `alembic upgrade head` from base on a clean DB fails earlier in the migration chain with `table sqlery_worker already exists` (a pre-existing duplicate `CreateTable` in earlier revisions). This plan's revision 0014 itself is correct (verified via stamp + isolated upgrade/downgrade); the chain-level error is pre-existing and out of scope.

## Known Stubs

None — both migrations and both model changes are fully wired.

## Threat Flags

None — this plan adjusts a status enum; no new network surface, auth path, or trust boundary.

## Self-Check: PASSED

- src/sqlery/django_sqlery/models.py — modified (verified)
- src/sqlery/core/models.py — modified (verified)
- src/sqlery/django_sqlery/migrations/0026_add_shutting_down_status.py — exists (verified)
- alembic/versions/20260514_0014_add_shutting_down_status.py — exists (verified)
- Commit e59bb94 — present in `git log`
- Commit 26cac4e — present in `git log`
