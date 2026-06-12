---
phase: 15-schema-cutover
fixed_at: 2026-06-12T00:00:00Z
review_path: .planning/phases/15-schema-cutover/15-REVIEW.md
iteration: 1
findings_in_scope: 5
fixed: 5
skipped: 0
status: all_fixed
---

# Phase 15: Code Review Fix Report

**Fixed at:** 2026-06-12
**Source review:** .planning/phases/15-schema-cutover/15-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 5 (CR-01, CR-02, CR-03, WR-01, WR-02)
- Fixed: 5
- Skipped: 0

Note: IN-01 (missing secondary indexes) was explicitly excluded per instructions — carried to Phase 16.

## Fixed Issues

### CR-01: FK constraints on sqlery_registry and sqlery_worker are never dropped

**Files modified:** `src/sqlery/django_sqlery/migrations/0030_partition_queued_job.py`
**Commit:** `3e08a51`
**Applied fix:** Added S0 step to `_VendorGuardedCutover._forward` (before S1). S0 executes a PL/pgSQL DO block that discovers all FK constraints referencing `sqlery_queued_job` via `information_schema` and drops each one with `ALTER TABLE %I DROP CONSTRAINT IF EXISTS %I`. This is idempotent (the catalog query returns no rows on re-run after the constraints are gone; `DROP CONSTRAINT IF EXISTS` is a no-op). Without this step, S1's rename caused PostgreSQL to redirect both FKs to `sqlery_queued_job_legacy`, making every post-cutover `JobRegistry` and `Worker` write with a new job id raise `IntegrityError`. Confirmed: the 6 pre-existing `test_django_backend.py` failures with "insert or update violates foreign key constraint" are now resolved.

---

### CR-02: Rollback table has no PK, ON CONFLICT DO NOTHING is a no-op on re-run

**Files modified:** `src/sqlery/django_sqlery/migrations/0030_partition_queued_job.py`
**Commit:** `3e08a51`
**Applied fix:** In `_backward`, after `CREATE TABLE IF NOT EXISTS sqlery_queued_job_unpartitioned`, added a catalog-guarded `ADD PRIMARY KEY (created_at, id)` so `ON CONFLICT DO NOTHING` can actually detect duplicates on re-run. Also guarded the step-3 rename target (`sqlery_queued_job_partitioned_bak`) with an `IS NULL` check so a re-run after a crash between steps 3 and 4 doesn't raise "relation already exists". The rollback is now fully idempotent: re-running after a partial failure yields the correct row count, not 2x. Confirmed by SC4b test (see WR-02 below).

---

### CR-03: job_name UNIQUE constraint silently lost after cutover; ORM state drift

**Files modified:** `src/sqlery/django_sqlery/migrations/0030_partition_queued_job.py`, `src/sqlery/django_sqlery/models.py`
**Commit:** `3e08a51`
**Applied fix (three parts):**
1. Added S3b to `_forward` after S3: `ALTER TABLE sqlery_queued_job DROP CONSTRAINT IF EXISTS sqlery_queued_job_job_name_key`. This is explicit/defensive — `LIKE INCLUDING DEFAULTS` doesn't copy constraints anyway, but the guard documents the intentional absence and prevents any edge case.
2. Added an `AlterField` state_operation in `SeparateDatabaseAndState` that removes `unique=True` from `QueuedJob.job_name`. This syncs Django's ORM state to match the physical schema (no more `makemigrations` drift). Verified with `python -m django makemigrations --check --skip-checks` returning "No changes detected".
3. Updated `models.py` QueuedJob.job_name to remove `unique=True` (old line commented out per CLAUDE.md convention), with a comment explaining that uniqueness is now enforced at application level in `backend.create_job` (new job wins: stop + delete conflicts).

---

### WR-01: BLAST-RADIUS-AUDIT.md marks async_backend.py items 10-18 as DEFERRED-PHASE-16 but they are already fixed

**Files modified:** `.planning/phases/15-schema-cutover/BLAST-RADIUS-AUDIT.md`
**Commit:** `3e08a51`
**Applied fix:** Updated all 9 entries (items 10–18) from `DEFERRED-PHASE-16` to `FIXED-15-03`, updating the actual current line numbers (e.g., item 10: line 102→104, item 11: line 134→138, etc.) and adding notes referencing the Phase 15 code review. Adjusted the Summary section: `DEFERRED-PHASE-16` count from 22 to 13; added `FIXED-15-03: 9`. `UNADDRESSED: 0` is preserved.

---

### WR-02: No test for rollback idempotency and no test for FK operability after cutover

**Files modified:** `tests/test_phase15_migration_roundtrip.py`
**Commit:** `ee639aa`
**Applied fix:** Added two new tests and a helper function:

**`_create_legacy_schema_with_real_constraints(conn)`** — builds the legacy schema with real FK constraints matching migrations 0002/0003 (`sqlery_worker.current_job_id FK → sqlery_queued_job.id` with ON DELETE SET NULL; `sqlery_registry.job_id FK → sqlery_queued_job.id` with ON DELETE CASCADE) and `job_name UNIQUE` from migration 0015. This is the schema the real migration operates on in production.

**`test_migration_fk_operability_sc6`** (SC6) — builds the legacy schema with real FK constraints, seeds a few rows, runs the 0030 forward cutover, then asserts: (1) new `QueuedJob` can be inserted into the partitioned table, (2) `sqlery_registry` row referencing the new job id inserts without `IntegrityError`, (3) `sqlery_worker.current_job_id` can be updated to the new job id, (4) job_name dedup at app level still works. This test detected CR-01 (assertions 2 and 3 failed before the S0 fix) and CR-03 (assertion 4 regression path). Now passes.

**`test_migration_rollback_idempotency_sc4b`** (SC4b) — runs forward migration, executes rollback steps 1+2 only (create table + insert, no rename), then re-runs full `_backward`. Asserts final row count equals original (not doubled). Without CR-02 fix, this fails with "SC4b FAIL: rollback re-run produced 200 rows (expected 100)". Now passes.

---

## Final Validation Results

**PG test tally (tests/test_phase15_migration_roundtrip.py):** 6 passed, 0 failed
```
test_sc5_blast_radius_audit_zero_unaddressed         PASSED
test_migration_forward_sc1_sc2_sc3                   PASSED
test_migration_rollback_sc1                          PASSED
test_migration_idempotency_sc4                       PASSED
test_migration_fk_operability_sc6         (NEW)      PASSED
test_migration_rollback_idempotency_sc4b  (NEW)      PASSED
```

**makemigrations --check (--skip-checks):** No changes detected (clean, no drift)
Note: `--skip-checks` needed because `BigAutoField(primary_key=False)` triggers pre-existing `fields.E100` system check that predates these changes.

**SQLite unit suite (tests/unit, no PG URL):** 500 passed, 11 skipped, 3 xfailed, 0 failures, 0 errors
Note: 7 pre-existing errors in `test_sqlalchemy_backend_sync.py` (PG-specific tests that error without the PG URL — unchanged from baseline).

**PG unit suite (tests/unit, with SQLERY_TEST_PG_URL):** 503 passed, 1 skipped, 3 xfailed, 7 errors (pre-existing SQLAlchemy backend errors, unchanged)
Bonus: 6 pre-existing `test_django_backend.py` IntegrityError failures (caused by CR-01) are now resolved by the S0 fix.

---

_Fixed: 2026-06-12_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
