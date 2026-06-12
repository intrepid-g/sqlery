---
phase: 15-schema-cutover
plan: "01"
subsystem: django-models
tags: [composite-pk, schema, fk-demotion, blast-radius-audit, partition-prep]
dependency_graph:
  requires: []
  provides: [BLAST-RADIUS-AUDIT.md, QueuedJob-composite-pk, JobRegistry-job-id, Worker-current-job-id]
  affects: [15-02-migration, 15-03-round-trip, phase-16-write-path-pruning]
tech_stack:
  added: []
  patterns: [CompositePrimaryKey, BigAutoField-non-pk, FK-demotion-BigIntegerField]
key_files:
  created:
    - .planning/phases/15-schema-cutover/BLAST-RADIUS-AUDIT.md
  modified:
    - src/sqlery/django_sqlery/models.py
    - src/sqlery/django_sqlery/admin.py
    - src/sqlery/django_sqlery/backend.py
    - src/sqlery/django_sqlery/registries.py
    - src/sqlery/django_sqlery/api_views.py
    - src/sqlery/django_sqlery/views.py
    - src/sqlery/django_sqlery/intervention.py
    - src/sqlery/django_sqlery/deadlines.py
    - src/sqlery/django_sqlery/worker_registry.py
    - src/sqlery/core/claiming.py
decisions:
  - "D4 enforced: JobRegistry.job and Worker.current_job FK references demoted to BigIntegerField; orphans on partition drop are accepted"
  - "Admin registration for QueuedJob changed from @admin.register decorator to manual admin.site.register() due to Django 5.2 composite-PK restriction"
  - "BLAST-RADIUS-AUDIT.md audit scope extended to include downstream FK traversal callers beyond the five primary model changes"
metrics:
  duration: "~45 minutes"
  completed: "2026-06-11"
  tasks_completed: 2
  tasks_total: 2
  files_changed: 11
---

# Phase 15 Plan 01: Blast-Radius Audit + Composite PK Model Changes Summary

CompositePrimaryKey("created_at","id") on QueuedJob, FK demotion of JobRegistry.job and Worker.current_job to BigIntegerField per D4, save_meta filter rewrite, and full blast-radius audit with 86 hits enumerated and 0 unaddressed.

## Tasks Completed

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | Blast-radius audit | ecc1e9b | .planning/phases/15-schema-cutover/BLAST-RADIUS-AUDIT.md |
| 2 | Model changes (CompositePK, FK demotion, save_meta) | 248c510 | models.py, admin.py, backend.py, registries.py, api_views.py, views.py, intervention.py, deadlines.py, worker_registry.py, claiming.py |

## Changes Made

### BLAST-RADIUS-AUDIT.md

86 hits enumerated across `src/sqlery/` and `tests/`:
- **FIXED-HERE: 23** — 5 primary model changes + 18 downstream FK traversal callers
- **DEFERRED-PHASE-16: 22** — async_backend pk= id-only filters; rq.py/scheduler.py .pk usages; test-side pk= QueuedJob lookups
- **N/A: 28** — ScheduledTask pk, Worker UUID pk, non-QueuedJob model operations
- **ACCEPTABLE: 13** — refresh_from_db on fully-loaded QueuedJob instances; Worker UUID pk; current_job_id direct int field
- **UNADDRESSED: 0** (acceptance criterion met)

### models.py — 5 primary changes

1. `pk = models.CompositePrimaryKey("created_at", "id")` added to QueuedJob (before task_path field)
2. `id = models.BigAutoField(primary_key=False)` added to QueuedJob (partition key comment block added)
3. `save_meta`: `filter(pk=self.pk)` commented out, `filter(id=self.id, created_at=self.created_at)` active
4. `JobRegistry.job` FK → `job_id = BigIntegerField(db_index=True)` with `#CLEANUP` annotation
5. `Worker.current_job` FK → `current_job_id = BigIntegerField(null=True, blank=True, db_index=True)` with `#CLEANUP` annotation

### Downstream FK traversal callers updated (FIXED-HERE audit items #40–62 extended)

All callers that traversed `Worker.current_job` (FK) or `JobRegistry.job` (FK) were updated:

- **models.py**: `Worker.objects.filter(current_job=self)` → `filter(current_job_id=self.id)`; `worker.current_job = None` → `worker.current_job_id = None`; `update(current_job=None)` → `update(current_job_id=None)`; update_fields lists updated
- **claiming.py**: `worker.current_job = job` → `worker.current_job_id = job.id`; all update_fields lists updated
- **backend.py**: `get_registry_jobs` — select_related("job") removed; entry.job traversal replaced with explicit QueuedJob id__in query; `worker_row.current_job = None` → `current_job_id = None`; update_fields updated
- **registries.py**: `JobRegistry.objects.create(job=job)` → `create(job_id=job.id)`; `filter(job=job)` → `filter(job_id=job.id)`; `select_related('job')` removed; `job__queue_name` traversal replaced with explicit subquery (queue_name filter is DEFERRED-PHASE-16 for select_related restoration)
- **api_views.py**: `select_related('current_job', 'current_job__scheduled_task')` removed; `worker.current_job` attribute replaced with explicit `QueuedJob.objects.get(id=worker.current_job_id)` fetch
- **views.py**: Two `select_related('current_job')` calls removed; `worker.current_job` attribute accesses replaced with explicit QueuedJob fetches; health-warning stall detection updated
- **intervention.py**: Three `worker.current_job = None` / `update_fields=['status', 'current_job']` patterns updated; `stale.update(current_job=None)` → `update(current_job_id=None)`
- **deadlines.py**: `.update(status='dead', current_job=None)` → `update(current_job_id=None)`
- **worker_registry.py**: `if worker.current_job` → `if worker.current_job_id` with explicit fetch; `query.update(current_job=None)` → `update(current_job_id=None)`
- **admin.py**: `@admin.register(QueuedJob)` decorator removed (Django 5.2 raises ImproperlyConfigured for composite-PK models); replaced with `admin.site.register(QueuedJob, QueuedJobAdmin)` at module end with try/except guard

## Verification Results

- `django.setup()` imports cleanly: PASS
- `QueuedJob._meta.pk` type = `CompositePrimaryKey`: PASS
- `QueuedJob.id` `primary_key=False`: PASS
- `JobRegistry.job_id` is `BigIntegerField`: PASS
- `Worker.current_job_id` is `BigIntegerField`: PASS
- `filter(id=self.id, created_at=self.created_at)` present in save_meta: PASS
- `BLAST-RADIUS-AUDIT.md` UNADDRESSED=0: PASS
- Unit test suite: **500 passed, 11 skipped, 3 xfailed, 0 new failures**

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing critical callers] FK demotion callers beyond plan's 5 primary changes**
- **Found during:** Task 2 execution and test run
- **Issue:** The plan listed 5 primary model changes but the FK demotion of `Worker.current_job` and `JobRegistry.job` required updating ~23 downstream callers across 9 files (intervention.py, deadlines.py, worker_registry.py, views.py, api_views.py, backend.py, registries.py, claiming.py, admin.py). These were all enumerated as FIXED-HERE in the blast-radius audit.
- **Fix:** All downstream callers updated with commented-out old lines and corrected lines beneath (per CLAUDE.md convention).
- **Files modified:** models.py, admin.py, backend.py, registries.py, api_views.py, views.py, intervention.py, deadlines.py, worker_registry.py, claiming.py
- **Commit:** 248c510

**2. [Rule 1 - Bug] Django 5.2 composite-PK admin registration**
- **Found during:** Task 2 django.setup() verification
- **Issue:** `@admin.register(QueuedJob)` raises `ImproperlyConfigured: The model QueuedJob has a composite primary key, so it cannot be registered with admin` in Django 5.2+.
- **Fix:** Removed `@admin.register` decorator from `QueuedJobAdmin`; added `admin.site.register(QueuedJob, QueuedJobAdmin)` at module end with try/except guard.
- **Files modified:** src/sqlery/django_sqlery/admin.py
- **Commit:** 248c510

## Known Stubs

None — all model changes are production-ready (no hardcoded empty values or placeholder data).

## Threat Flags

No new security-relevant surface introduced. This plan removes FK constraints (D4-accepted trade-off) and adds a composite PK — no new network endpoints, auth paths, or trust boundary changes.

## Self-Check: PASSED

- BLAST-RADIUS-AUDIT.md exists: FOUND
- Commit ecc1e9b exists: FOUND
- Commit 248c510 exists: FOUND
- `CompositePrimaryKey` in models.py: FOUND
- `filter(id=self.id, created_at=self.created_at)` in models.py: FOUND
- `job_id = models.BigIntegerField` in models.py: FOUND
- `current_job_id = models.BigIntegerField` in models.py: FOUND
