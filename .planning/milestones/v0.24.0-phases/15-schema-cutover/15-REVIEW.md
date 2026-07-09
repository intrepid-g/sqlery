---
phase: 15-schema-cutover
reviewed: 2026-06-12T00:00:00Z
depth: deep
files_reviewed: 4
files_reviewed_list:
  - src/sqlery/django_sqlery/models.py
  - src/sqlery/django_sqlery/migrations/0029_scheduled_job_staging.py
  - src/sqlery/django_sqlery/migrations/0030_partition_queued_job.py
  - tests/test_phase15_migration_roundtrip.py
findings:
  critical: 3
  warning: 2
  info: 1
  total: 6
status: issues_found
---

# Phase 15: Code Review Report

**Reviewed:** 2026-06-12
**Depth:** deep
**Files Reviewed:** 4
**Status:** issues_found

## Summary

The composite PK / FK demotion work in `models.py` is sound. The `save_meta` rewrite,
all FIXED-HERE items in the blast-radius audit, and every active-path downstream caller
(claiming.py, backend.py, registries.py, api_views.py, views.py, intervention.py,
deadlines.py, worker_registry.py, admin.py, async_backend.py) were reviewed and the
flaw patterns are gone from those paths.

Migration 0029 (shared-sequence wiring) is correct: idempotent DDL, correct setval
semantics (`is_called=false` for empty table, `is_called=true` with max id otherwise),
and sound reverse ordering (defaults dropped before sequence drop). The vendor guard
is correctly placed.

Migration 0030 forward path (S1–S9) is largely sound: S1 idempotency guard, S2 LIKE
INCLUDING DEFAULTS, S3 catalog-guarded ADD PK, S5/S7 IF NOT EXISTS guards, S8
ON CONFLICT DO NOTHING, S9 sequence re-seed — all idempotent on a second run.
Django's psycopg3 backend uses `ClientCursor` (client-side binding), so S7's
parameterized partition DDL is safe in production, matching what the test uses.

Three critical defects are present. Two cause complete runtime breakage after the migration
runs; one causes silent data-model drift. Neither is covered by the existing test suite (SC1–SC5).

---

## Critical Issues

### CR-01: FK constraints on `sqlery_registry` and `sqlery_worker` are never dropped — all post-cutover job execution breaks

**Files:** `src/sqlery/django_sqlery/migrations/0030_partition_queued_job.py` — `_VendorGuardedCutover._forward`

**Issue:**
`JobRegistry.job` (DB column `job_id`) and `Worker.current_job` (DB column `current_job_id`)
were created as FK columns in migrations 0003 and 0002 respectively. Their FK constraints in the
physical DB reference `sqlery_queued_job(id)`.

Migration 0030 uses `SeparateDatabaseAndState(database_operations=[])` to model the demotion —
this updates Django's ORM state but runs **no DDL**. The FK constraints are **never dropped
from the physical DB**.

When S1 renames `sqlery_queued_job → sqlery_queued_job_legacy`, PostgreSQL automatically
redirects both FK constraints to reference `sqlery_queued_job_legacy`. After cutover, all new
jobs land in the new partitioned `sqlery_queued_job`; the legacy table receives no further inserts.

Consequences after the migration completes:

1. **Every `JobRegistry.objects.create(job_id=X, ...)` call for a new job** raises
   `IntegrityError` — `X` is not in `sqlery_queued_job_legacy`. Since `ENABLE_REGISTRIES`
   defaults to `True`, this fires on every `track_job_start()` / `track_job_finish()` call,
   which is every job execution.

2. **Every `worker.save(update_fields=["status", "current_job_id"])` call** with a new job ID
   raises `IntegrityError` — fired in `claim_next_job_with_queue_priority` after every successful
   claim. Workers cannot claim any new job.

**Fix:**
Add a step S0 to `_VendorGuardedCutover._forward` (before S1) that drops both FK constraints
via a catalog query so the exact auto-generated constraint name does not need to be hard-coded:

```sql
-- S0: Drop FK constraints that reference sqlery_queued_job before renaming it.
-- Constraints are looked up from pg_catalog so auto-generated names need not be hard-coded.
DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT tc.table_name, tc.constraint_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.referential_constraints rc
          ON tc.constraint_name = rc.constraint_name
        JOIN information_schema.constraint_column_usage ccu
          ON rc.unique_constraint_name = ccu.constraint_name
        WHERE ccu.table_name = 'sqlery_queued_job'
          AND tc.constraint_type = 'FOREIGN KEY'
    LOOP
        EXECUTE format(
            'ALTER TABLE %I DROP CONSTRAINT IF EXISTS %I',
            r.table_name, r.constraint_name
        );
    END LOOP;
END $$;
```

The reverse (`_backward`) does not need to restore these constraints — D4 documents that FK
referential integrity to the jobs table is intentionally dropped.

---

### CR-02: Rollback `_backward` has no primary key on the unpartitioned copy table — duplicate rows on re-run after partial failure

**File:** `src/sqlery/django_sqlery/migrations/0030_partition_queued_job.py:285-316`

**Issue:**
`_backward` creates the rollback table with:
```sql
CREATE TABLE IF NOT EXISTS sqlery_queued_job_unpartitioned (
    LIKE sqlery_queued_job INCLUDING DEFAULTS INCLUDING STORAGE
);
```
`LIKE INCLUDING DEFAULTS INCLUDING STORAGE` does **not** copy constraints or indexes
(`INCLUDING CONSTRAINTS` is required for that). The unpartitioned rollback table is created
with **no primary key** and **no unique constraints**.

The subsequent:
```sql
INSERT INTO sqlery_queued_job_unpartitioned
    SELECT * FROM sqlery_queued_job
    ON CONFLICT DO NOTHING;
```
`ON CONFLICT DO NOTHING` **without a unique constraint is a complete no-op** — it never
detects a conflict. Every re-run of `_backward` appends all rows again, doubling the count.

Failure scenario (crash between step 3 and step 4 of `_backward`):
- `sqlery_queued_job` was renamed to `sqlery_queued_job_partitioned_bak`
- `sqlery_queued_job_unpartitioned` exists with 1 M rows
- Re-run of `_backward`:
  - Step 1 (`IF NOT EXISTS`): skipped
  - Step 2 (`INSERT ON CONFLICT`): inserts all 1 M rows again → 2 M rows
  - Step 3 (rename guard): `sqlery_queued_job` IS NULL → skipped
  - Step 4 (rename `_unpartitioned → sqlery_queued_job`): succeeds with 2 M rows

The resulting `sqlery_queued_job` has every row duplicated.

Additionally, if the rollback is re-run after a crash before step 3, step 3's rename will
fail with `relation "sqlery_queued_job_partitioned_bak" already exists` if a prior attempt
already created it (no guard on the target name).

**Fix:**
Add a primary key to the rollback table and guard the step-3 rename target:

```python
# Step 1 — also add PK so ON CONFLICT can detect duplicates
cursor.execute(
    """
    CREATE TABLE IF NOT EXISTS sqlery_queued_job_unpartitioned (
        LIKE sqlery_queued_job INCLUDING DEFAULTS INCLUDING STORAGE
    );
    """
)
# Add PK if not present (idempotency guard)
cursor.execute(
    """
    SELECT COUNT(*) FROM pg_constraint
    WHERE conrelid = 'sqlery_queued_job_unpartitioned'::regclass AND contype = 'p'
    """
)
(pk_count,) = cursor.fetchone()
if pk_count == 0:
    cursor.execute(
        "ALTER TABLE sqlery_queued_job_unpartitioned "
        "ADD PRIMARY KEY (created_at, id);"
    )

# Step 2 — unchanged; now ON CONFLICT actually fires on (created_at, id) duplicates

# Step 3 — guard target name too
cursor.execute(
    """
    DO $$ BEGIN
        IF to_regclass('public.sqlery_queued_job') IS NOT NULL
           AND to_regclass('public.sqlery_queued_job_partitioned_bak') IS NULL THEN
            ALTER TABLE sqlery_queued_job RENAME TO sqlery_queued_job_partitioned_bak;
        END IF;
    END $$;
    """
)
```

---

### CR-03: `job_name` unique constraint silently dropped by `LIKE INCLUDING DEFAULTS` — DB-level uniqueness lost after cutover

**File:** `src/sqlery/django_sqlery/migrations/0030_partition_queued_job.py:108-115`

**Issue:**
Step S2 creates the partitioned table with:
```sql
CREATE TABLE IF NOT EXISTS sqlery_queued_job (
    LIKE sqlery_queued_job_legacy
    INCLUDING DEFAULTS INCLUDING STORAGE
) PARTITION BY RANGE (created_at);
```

PostgreSQL's `LIKE ... INCLUDING DEFAULTS INCLUDING STORAGE` copies column definitions and
defaults only. It does **not** copy constraints or indexes — `INCLUDING CONSTRAINTS` or
`INCLUDING INDEXES` would be required. The `job_name` column has `UNIQUE` in the legacy table
(added in migration 0015). After cutover:

- The physical unique constraint on `job_name` is **silently dropped**.
- Django's ORM state still declares `unique=True` (the `state_operations` do not remove it).
- `QueuedJob.objects.get(job_name=X)` will continue to work until a duplicate is inserted;
  then it raises `MultipleObjectsReturned` at unpredictable future points.
- `force_stop()` and any code using `get_by_name()` can encounter duplicates silently.

Note: a global unique constraint on `job_name` alone **cannot** exist on a PG partitioned table
(PG requires all partition-key columns in the unique set). The correct fix is either to drop the
constraint explicitly and document it, or (if uniqueness is needed) recreate it as a
partial unique index per-partition. The current migration does neither — the uniqueness is simply
lost with no documentation or compensating measure.

**Fix (minimal — document the intentional drop and remove the ORM drift):**

In `_forward`, after S3, add:
```sql
-- S3b: Drop the job_name unique constraint if it was copied (it cannot exist on
-- a partitioned table without the partition key; PG 11+ silently rejects it).
-- LIKE INCLUDING DEFAULTS does not copy it anyway, but this guard is explicit.
-- job_name uniqueness is enforced at application level after cutover.
ALTER TABLE sqlery_queued_job
    DROP CONSTRAINT IF EXISTS sqlery_queued_job_job_name_key;
```

And add a `state_operations` `AlterField` to remove `unique=True` from `job_name`:
```python
migrations.AlterField(
    model_name="queuedjob",
    name="job_name",
    field=models.CharField(
        max_length=255,
        null=True,
        blank=True,
        db_index=True,
        # unique intentionally removed: global uniqueness across partitions requires
        # the partition key in the constraint, which job_name does not include (D4 note).
        help_text="Optional unique string identifier (e.g. 'send-invoice-123')",
    ),
),
```

---

## Warnings

### WR-01: BLAST-RADIUS-AUDIT.md marks `async_backend.py` items 10–18 as `DEFERRED-PHASE-16` but they are already fixed

**File:** `.planning/phases/15-schema-cutover/BLAST-RADIUS-AUDIT.md:25-33` vs.
`src/sqlery/django_sqlery/async_backend.py:102-204`

**Issue:**
The audit document records `async_backend.py` items #10–18 (lines 102, 134, 145, 151, 157,
167, 175, 181, 188 in the OLD version) as `DEFERRED-PHASE-16`. The current file shows all of
these have been rewritten (`pk=` → `id=`) as part of Phase 15 work. For example:
- Audit item 10: "line 102 `await QueuedJob.objects.aget(pk=job_id)`" → actual line 104:
  `return await QueuedJob.objects.aget(id=job_id)` ✓

The SC5 test only checks `UNADDRESSED: 0`, which still passes, but Phase 16 will waste effort
re-verifying already-fixed items, and the stale `DEFERRED` status creates false confidence
that these paths are broken when they are not.

**Fix:** Update items #10–18 in `BLAST-RADIUS-AUDIT.md` from `DEFERRED-PHASE-16` to
`FIXED-HERE` (or `FIXED-15-03`) and adjust the count row accordingly.

---

### WR-02: No test for rollback idempotency — the duplicate-row hazard in CR-02 is undetected

**File:** `tests/test_phase15_migration_roundtrip.py`

**Issue:**
SC4 tests forward idempotency (crash after S1, rerun completes). There is no equivalent
test for backward idempotency: no test crashes `_backward` after step 2 or 3 and then
re-runs it to verify that the row count is preserved (not doubled).

Given that CR-02 proves a re-run of `_backward` after a crash between steps 3 and 4 will
double all rows, this test gap means the rollback hazard has no automated detection.

**Fix:** Add `test_migration_rollback_idempotency_sc4b` to the test file:
- Run `_forward`.
- Execute rollback steps 1–2 (create table + insert rows), then simulate crash.
- Re-run full `_backward`.
- Assert final row count equals the pre-rollback count, not 2×.

This test should be blocked by CR-02 until that fix is applied.

---

## Info

### IN-01: All db_index=True field indexes are silently dropped from the partitioned table

**File:** `src/sqlery/django_sqlery/migrations/0030_partition_queued_job.py:108-115`

**Issue:**
`LIKE ... INCLUDING DEFAULTS INCLUDING STORAGE` does not include indexes. Only
`sqlery_job_pending_idx` is explicitly recreated (S6). All single-column indexes from
`db_index=True` fields — `queue_name`, `priority`, `status`, `scheduled_at`, `parent_job_id`,
`job_name`, `worker_id` — are silently absent from the new partitioned table.

This is a **performance** concern (full-partition-scan on queries by status, scheduled_at, etc.)
that is explicitly out of scope for this review per the v1 rules. It is recorded here as
information for Phase 16's write-path pruning task, which should also include an explicit
index-recreation step for the partitioned table.

**Fix (Phase 16 carry-forward):** Add explicit `CREATE INDEX` statements in `_forward` for
each `db_index=True` field, or add a follow-on migration that recreates them using
`AddIndexConcurrently`.

---

## Migration Idempotency Assessment

Forward path (S1–S9): **idempotent** on re-run after partial failure at every step, including
S6's `DROP IF EXISTS` + `CREATE` sequence. The one exception is that S0 (the FK drop fix from
CR-01) must also be made idempotent — the catalog-query approach above is safe on re-run.

Backward path: **not idempotent** on re-run after a crash between steps 3 and 4 (CR-02). Fix
required before this migration can be considered rollback-safe.

---

_Reviewed: 2026-06-12_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
