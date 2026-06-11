---
phase: 14-scheduled-job-staging
plan: "01"
subsystem: django-models
tags:
  - django
  - migration
  - scheduled-job
  - staging
dependency_graph:
  requires:
    - 12-01 (0028_partial_pending_index migration)
  provides:
    - ScheduledJob Django model
    - Migration 0029_scheduled_job_staging
  affects:
    - src/sqlery/django_sqlery/models.py
    - src/sqlery/django_sqlery/migrations/
tech_stack:
  added: []
  patterns:
    - _PgSequenceWiring guard class (vendor-conditional RunSQL)
key_files:
  created:
    - src/sqlery/django_sqlery/migrations/0029_scheduled_job_staging.py
  modified:
    - src/sqlery/django_sqlery/models.py
decisions:
  - "Migration numbered 0029 (staging) not 0030 — user-locked decision from STATE.md"
  - "Shared id sequence wired via _PgSequenceWiring subclass; skipped on SQLite"
  - "ScheduledJob placed after DaemonLease and before backward-compat alias"
metrics:
  duration: "~12 minutes"
  completed: "2026-06-11"
  tasks_completed: 2
  tasks_total: 2
  files_changed: 2
---

# Phase 14 Plan 01: ScheduledJob Model + Migration 0029 Summary

ScheduledJob staging table added (slim 8-field model) with migration 0029 that shares sqlery_queued_job_id_seq on PostgreSQL via a vendor-guarded RunSQL subclass.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Add ScheduledJob model | a5259bd | src/sqlery/django_sqlery/models.py |
| 2 | Write migration 0029 | 3770f3f | src/sqlery/django_sqlery/migrations/0029_scheduled_job_staging.py |

## What Was Built

**ScheduledJob model** (`src/sqlery/django_sqlery/models.py`):
- 8 fields: `id` (BigAutoField PK), `queue_name`, `task_path`, `payload` (JSONField), `scheduled_at`, `priority`, `max_retries`, `created_at`
- `Meta.db_table = 'sqlery_scheduled_job'`, `ordering = ['scheduled_at']`
- Index on `scheduled_at` named `sqlery_staged_job_sched_idx` for promotion range scans
- `DjangoJSONEncoder` on payload (no new import — already present at module level)

**Migration 0029** (`src/sqlery/django_sqlery/migrations/0029_scheduled_job_staging.py`):
- `dependencies = [('sqlery', '0028_partial_pending_index')]` — user-locked numbering
- `atomic = True` (plain DDL, no concurrent index ops)
- `CreateModel` operation produces `sqlery_scheduled_job` with all 8 fields and the scheduled_at index
- `_PgSequenceWiring(migrations.RunSQL)` subclass: skips on non-PostgreSQL vendors; on PostgreSQL executes:
  - `ALTER SEQUENCE IF EXISTS sqlery_scheduled_job_id_seq OWNED BY NONE;`
  - `ALTER TABLE sqlery_scheduled_job ALTER COLUMN id SET DEFAULT nextval('sqlery_queued_job_id_seq'::regclass);`
- Reverse SQL drops the DEFAULT and recreates the own sequence

## Verification Results

- `ScheduledJob` importable from `sqlery.django_sqlery.models` — PASS
- All 8 fields present, `db_table == 'sqlery_scheduled_job'` — PASS
- `python -m django migrate` applies 0028 then 0029 cleanly on SQLite — PASS
- Dependency chain `0028 → 0029` confirmed — PASS
- SQLite: sequence ALTER is no-op (`-- (no-op)` in `sqlmigrate` output) — PASS

## Deviations from Plan

None — plan executed exactly as written.

The worktree required a `git reset --hard 3c5b742` at startup because the worktree branch
(`33eee21`) predated the 0028 migration. This was specified in the `<worktree_branch_check>`
of the execution instructions and is not a plan deviation.

## Known Stubs

None — the model is a pure data class; no data-sourcing or rendering is involved in this plan.

## Threat Flags

No new security-relevant surface beyond the migration DDL. The sequence ALTER uses a literal
string constant (`sqlery_queued_job_id_seq`) — not user-supplied — satisfying T-14-01.

## Self-Check: PASSED

- `src/sqlery/django_sqlery/models.py` — modified, contains `class ScheduledJob`
- `src/sqlery/django_sqlery/migrations/0029_scheduled_job_staging.py` — created
- Commit `a5259bd` — present (`git log --oneline | grep a5259bd`)
- Commit `3770f3f` — present (`git log --oneline | grep 3770f3f`)
