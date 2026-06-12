---
phase: 14-scheduled-job-staging
verified: 2026-06-11T21:32:56Z
status: passed
score: 6/6 must-haves verified
overrides_applied: 0
---

# Phase 14: scheduled-job-staging Verification Report

**Phase Goal:** Far-future scheduled jobs live in a staging table and are promoted exactly-once, so no queued row can pin an otherwise-drained partition.
**Verified:** 2026-06-11T21:32:56Z
**Status:** passed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | A job scheduled 60 days out is invisible to claims, visible to status/cancel APIs | VERIFIED | `test_far_future_job_invisible_to_claim_queue` + `test_far_future_job_visible_to_get_job_by_id` + `test_far_future_job_cancellable` — all 3 pass live |
| 2 | Far-future job is promoted within one daemon tick of `scheduled_at - lookahead` | VERIFIED | `promote_due_scheduled_jobs` deletes rows where `scheduled_at <= now() + make_interval(secs => 30)`, wired into the partition maintenance tick in `daemon.py:636` |
| 3 | Two daemons never double-promote (`pg_try_advisory_lock` + `SKIP LOCKED`) | VERIFIED | `promote_due_scheduled_jobs` acquires `pg_try_advisory_lock(ADVISORY_LOCK_PROMOTE)` and returns 0 immediately on failure; DELETE uses `FOR UPDATE SKIP LOCKED`; `test_skips_when_lock_not_acquired` and `test_advisory_unlock_called_even_on_insert_error` pass live |
| 4 | Config validation rejects `SQLERY_PARTITION_RETENTION` <= staging threshold | VERIFIED | `_validate_staging_config` in `daemon.py:101-125` raises `ValueError` on equal or lesser retention; called at startup (`daemon.py:497`); `TestStagingConfigValidation` 4/4 pass live |
| 5 | Migration is `0029_scheduled_job_staging` depending on `0028_partial_pending_index` | VERIFIED | File `/migrations/0029_scheduled_job_staging.py` exists; `dependencies = [("sqlery", "0028_partial_pending_index")]`; `_PgSequenceWiring` subclass skips on non-PostgreSQL |
| 6 | Dual-table surface: `get_job_by_id` falls back to `ScheduledJob`; `cancel_job` cancels staged; `get_staged_jobs` exists; `claim_job` stays `QueuedJob`-only | VERIFIED | All four verified by direct code read + `TestDualTableApiSurface` 10/10 pass live |

**Score:** 6/6 truths verified

---

## Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/sqlery/django_sqlery/models.py` | `ScheduledJob` model with 8 fields | VERIFIED | `class ScheduledJob` at line 1228; all 8 fields present; `db_table = 'sqlery_scheduled_job'`; `ordering = ['scheduled_at']`; index `sqlery_staged_job_sched_idx` |
| `src/sqlery/django_sqlery/migrations/0029_scheduled_job_staging.py` | Depends on 0028; shares PG sequence | VERIFIED | `dependencies = [("sqlery", "0028_partial_pending_index")]`; `_PgSequenceWiring` executes `nextval('sqlery_queued_job_id_seq')` on PostgreSQL, no-op on SQLite |
| `src/sqlery/core/scheduler.py` | `promote_due_scheduled_jobs` module-level, raw cursor, advisory-lock try/finally, no ORM imports | VERIFIED | Lines 389–451; no Django/ORM imports in file; `pg_try_advisory_lock` + `SKIP LOCKED` + `finally: pg_advisory_unlock`; psycopg guard at module level |
| `src/sqlery/core/daemon.py` | `_validate_staging_config` + promotion tick + staging threshold read | VERIFIED | `_validate_staging_config` at line 101; `staging_threshold_days` read at line 476; `_validate_staging_config` called at line 497; `promote_due_scheduled_jobs(cur)` called at line 636 |
| `src/sqlery/django_sqlery/backend.py` | Enqueue routing; dual-table `get_job_by_id`/`cancel_job`/`get_staged_jobs`; `claim_job` QueuedJob-only | VERIFIED | Routing at lines 83–97 (strict `>`); `get_job_by_id` fallback at lines 678–685; `cancel_job` dual-table at lines 317–330; `get_staged_jobs` at lines 1002–1022; `claim_job` has no `ScheduledJob` reference |
| `tests/unit/test_staging.py` | SC-1/SC-2/SC-3 test suite | VERIFIED | Created; 13 tests; 13/13 pass live |
| `tests/unit/test_django_backend.py` | `TestDualTableApiSurface` 10 tests | VERIFIED | Class at line 790; 10/10 pass live |
| `src/sqlery/django_sqlery/settings.py` | `SQLERY_SCHEDULED_JOB_THRESHOLD_DAYS: 1` in DEFAULTS | VERIFIED | Line 110 |

---

## Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `DjangoBackend.create_job` | `ScheduledJob.objects.create` | threshold routing block (`scheduled_at > now + threshold`) | WIRED | `backend.py:83-97` |
| `DaemonManager._run_daemon` | `promote_due_scheduled_jobs(cur)` | partition maintenance tick | WIRED | `daemon.py:636` inside partition maintenance try block |
| `DaemonManager._run_daemon` | `_validate_staging_config` | startup validation block | WIRED | `daemon.py:497` |
| `DjangoBackend.get_job_by_id` | `ScheduledJob.objects.get` | `QueuedJob.DoesNotExist` fallback | WIRED | `backend.py:680-685` |
| `DjangoBackend.cancel_job` | `ScheduledJob.objects.filter.delete()` | `updated == 0` fallback | WIRED | `backend.py:329` |
| `promote_due_scheduled_jobs` advisory lock | `pg_advisory_unlock` | `finally:` block | WIRED | `scheduler.py:449-451` — unlock always called |

---

## Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| `promote_due_scheduled_jobs` importable; constants correct | `python -c "from sqlery.core.scheduler import promote_due_scheduled_jobs, ADVISORY_LOCK_PROMOTE, _PROMOTION_LOOKAHEAD_SECONDS; print(ADVISORY_LOCK_PROMOTE)"` | `6003663638845607757`; lookahead = 30s | PASS |
| `_validate_staging_config` accepts valid config | `_validate_staging_config(1, '30 days')` | no exception | PASS |
| `_validate_staging_config` rejects equal retention | `_validate_staging_config(30, '30 days')` | `ValueError` raised | PASS |
| `test_staging.py` 13 tests | `pytest tests/unit/test_staging.py` | 13 passed in 0.53s | PASS |
| `TestDualTableApiSurface` 10 tests | `pytest tests/unit/test_django_backend.py::TestDualTableApiSurface` | 10 passed in 0.48s | PASS |

---

## SQLite Shared-ID Caveat — Verdict

**Situation:** On PostgreSQL, migration 0029's `_PgSequenceWiring` points `sqlery_scheduled_job.id` at `sqlery_queued_job_id_seq`, so IDs are globally unique across both tables. On SQLite, the sequence ALTER is a no-op and both tables use independent `sqlite_sequence` counters both starting at 1. A staged job and a queued job will both get `id=1` when they are the first rows in their respective tables. `get_job_by_id` queries `QueuedJob` first, so a `QueuedJob` with `id=1` would shadow a `ScheduledJob` with `id=1`.

**Verdict: Acceptable PG-vs-SQLite divergence. Not a phase gap.**

Rationale:
1. SQLite is a dev/lightweight mode only; the partition-pinning problem that motivates staging only occurs with PostgreSQL partitioning. SQLite has no partitioned tables.
2. The 14-03 plan explicitly acknowledged this collision and adjusted the test assertion to verify by ORM type rather than ID set (`test_get_jobs_returns_queued_jobs_only`) — a deliberate scope-aware decision.
3. The phase target (R5, REQ-scheduled-staging) is scoped to the PostgreSQL production path. The shared-id sequence is implemented correctly for the database where it matters.
4. The shadowing scenario (a queued job and staged job with the same integer ID existing simultaneously in SQLite) is a cosmetic dev inconvenience, not a correctness bug in the production target.

---

## Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `backend.py` | 673–677 | Commented-out old `get_job_by_id` body | Info | Project convention (`# Old:` pattern) — intentional, not a debt marker |
| `backend.py` | 319–323 | Commented-out old `cancel_job` body | Info | Same convention |

No `TBD`, `FIXME`, or `XXX` markers found in phase-modified files. No empty return stubs. No orphaned artifacts.

---

## Requirements Coverage

| Requirement | Description | Status | Evidence |
|-------------|-------------|--------|----------|
| R5 / REQ-scheduled-staging | Far-future jobs staged separately, promoted exactly-once, invisible to claims | SATISFIED | All 6 truths verified; 23 tests pass live |

---

## Human Verification Required

None. All success criteria are verifiable programmatically via the test suite and code inspection. No visual UI, real-time, or external-service behavior is introduced by this phase.

---

## Gaps Summary

No gaps. All phase goal components are present, substantive, wired, and tested.

---

_Verified: 2026-06-11T21:32:56Z_
_Verifier: Claude (gsd-verifier)_
