---
phase: 17-fastapi-parity
plan: "02"
subsystem: standalone-partition-ddl
tags:
  - database.py
  - alembic
  - partitioning
  - postgresql
  - standalone

dependency_graph:
  requires:
    - 17-01  # composite-PK QueuedJob + ScheduledJob SQLModel
  provides:
    - partitioned-sqlery-queued-job-fresh-install
    - alembic-cutover-revision-0016
  affects:
    - src/sqlery/fastapi_sqlery/database.py
    - alembic/versions/
    - src/sqlery/tables.py

tech_stack:
  added: []
  patterns:
    - "PARTITION BY RANGE(created_at) + DEFAULT partition + daily lookahead window"
    - "Shared sequence sqlery_job_id_seq (standalone mirror of Django migration 0029)"
    - "FK demotion: sqlery_worker.current_job_id + sqlery_registry.job_id created as plain BIGINT on PG (D4)"
    - "Vendor guard: `engine.dialect.name == 'postgresql'` in database.py; `op.get_bind().dialect.name` in Alembic"

key_files:
  created:
    - alembic/versions/20260612_0016_partition_queued_job.py
  modified:
    - src/sqlery/fastapi_sqlery/database.py
    - src/sqlery/tables.py

decisions:
  - "D4 demotion applied in _init_partitioned_pg: sqlery_worker and sqlery_registry are created via raw SQL without FK to sqlery_queued_job.id because PG partitioned tables cannot be referenced by single-column FK (only composite PK is unique)"
  - "D6 intact: SQLite path keeps plain SQLModel.metadata.create_all unchanged"
  - "D7 intact: sqlery_job_pending_idx DDL byte-identical to Django 0028"
  - "D8: PG dialect detection gates partitioned path in init_database()"
  - "Fresh install step ordering: create partitioned jobs table first (with sequence), then create remaining tables (checkfirst=True); FK-demoted tables created manually last"
  - "Partition date literals in FOR VALUES FROM/TO are f-string interpolated (not parameterized) because PG DDL cannot use $1/$2 binds for partition bounds — values are from Python date.strftime (digits/dashes only, safe)"

metrics:
  duration: ~45 min
  completed: "2026-06-12"
  tasks_completed: 2
  tasks_total: 2
  files_modified: 3
---

# Phase 17 Plan 02: Partitioned DDL for standalone fresh install + Alembic cutover revision

**One-liner:** PG fresh install now creates PARTITION BY RANGE(created_at) jobs table with shared sqlery_job_id_seq; Alembic revision 0016 cutovers existing plain-table installs idempotently.

## What Was Built

### Task 1: database.py partitioned DDL + tables.py SCHEDULED_JOB constant

`tables.py` gained `SCHEDULED_JOB = "sqlery_scheduled_job"`.

`database.py` gained two new private functions:

- `_build_partitioned_jobs_ddl()` — returns the canonical CREATE TABLE ... PARTITION BY RANGE(created_at) DDL with composite PK (created_at, id) and id DEFAULT nextval('sqlery_job_id_seq'). Column list is a static hard-coded canonical set that mirrors QueuedJob in core/models.py.

- `_init_partitioned_pg(engine)` — the PG fresh-install orchestrator. Steps:
  1. CREATE SEQUENCE IF NOT EXISTS sqlery_job_id_seq
  2. Inspect pg_class relkind: `p` (already partitioned, skip); `r` (plain table exists, inline cutover S0–S9 mirrors Django 0030); None (fresh install, emit partitioned DDL directly)
  3. CREATE DEFAULT partition (IF NOT EXISTS)
  4. Daily partition window: today + PREMAKE=7 days ahead (IF NOT EXISTS each)
  5. CREATE INDEX IF NOT EXISTS sqlery_job_pending_idx (D7 byte-identical)
  6. create_all (checkfirst=True) for remaining tables
  7. Create sqlery_worker and sqlery_registry via raw SQL without FK (D4 demotion — partitioned table cannot back single-column FK)

`init_database()` now branches: SQLite → plain SQLModel.metadata.create_all (D6); PG dialect → `_init_partitioned_pg`; other dialects → plain create_all fallback.

### Task 2: Alembic revision 20260612_0016_partition_queued_job.py

Chains after 20260608_0015. Mirrors Django 0030 S0–S9 for existing standalone PG installs:

- S0: Drop FK constraints referencing sqlery_queued_job (information_schema catalog query, DO block loop, idempotent)
- S1: Idempotent rename to sqlery_queued_job_legacy (to_regclass guards)
- Seq: CREATE SEQUENCE IF NOT EXISTS sqlery_job_id_seq + ALTER TABLE ... SET DEFAULT nextval on legacy
- S2: CREATE TABLE sqlery_queued_job (LIKE legacy INCLUDING DEFAULTS INCLUDING STORAGE) PARTITION BY RANGE(created_at)
- S3: ADD PRIMARY KEY (created_at, id) — catalog-guarded
- S3b: DROP CONSTRAINT IF EXISTS sqlery_queued_job_job_name_key
- S4: REMOVED (shared-sequence default already copied by S2 LIKE INCLUDING DEFAULTS)
- S5: CREATE DEFAULT partition
- S6: DROP INDEX IF EXISTS sqlery_job_pending_idx; CREATE INDEX sqlery_job_pending_idx (D7)
- S7: Historical + lookahead daily partitions (MIN/MAX of legacy rows + PREMAKE=7)
- S8: INSERT ... SELECT ON CONFLICT DO NOTHING (idempotent bulk copy)
- S9: setval('sqlery_job_id_seq', GREATEST(max(id), 1))

downgrade() creates unpartitioned rollback copy, copies rows back, renames partitioned table to _partitioned_bak, promotes unpartitioned copy. Legacy table retained (D3).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] FK cycle prevents non-jobs create_all first pass**
- **Found during:** Task 1 verification
- **Issue:** `SQLModel.metadata.sorted_tables` cycle warning + `InvalidForeignKey` error: sqlery_registry and sqlery_worker carry FKs to sqlery_queued_job.id; PG partitioned tables cannot be referenced by single-column FK (only composite PK (created_at, id) is unique). Running create_all for "non-jobs tables first" fails because those tables depend on the jobs table.
- **Fix:** Reversed order — create partitioned jobs table first (Step 1), then create all other tables via create_all(checkfirst=True) excluding sqlery_worker and sqlery_registry. Those two tables are created manually via raw SQL without FK constraints (D4 demotion, matching Django 0030 pattern).
- **Files modified:** src/sqlery/fastapi_sqlery/database.py
- **Commit:** d3ea683

**2. [Rule 1 - Bug] Parameterized binds rejected in FOR VALUES FROM/TO DDL**
- **Found during:** Task 1 verification (first iteration)
- **Issue:** psycopg3 raises `IndeterminateDatatype` when `:from_ts`/`:to_ts` bound params are passed to CREATE TABLE ... FOR VALUES FROM (:x) TO (:y) — PostgreSQL DDL cannot infer bind parameter types in partition bounds.
- **Fix:** f-string interpolation using Python `date.isoformat()` values (YYYY-MM-DD — digits and dashes only, safe for interpolation). Comment added to explain why.
- **Files modified:** src/sqlery/fastapi_sqlery/database.py
- **Commit:** d3ea683

## Verification Results

| Check | Result |
|-------|--------|
| Fresh PG install: `relkind = 'p'` | PASS |
| DEFAULT partition present | PASS |
| `sqlery_job_pending_idx` present (D7) | PASS |
| `sqlery_job_id_seq` present | PASS |
| 27 child partitions (default + daily window) | PASS |
| All non-jobs tables created | PASS |
| SELECT on partitioned table | PASS |
| Idempotent re-run | PASS |
| Alembic upgrade 0016 on existing plain schema | PASS |
| Data copied (1 row) to partitioned table | PASS |
| Legacy table preserved after Alembic upgrade | PASS |
| Alembic revision/down_revision metadata | PASS |
| SQLite core tests (test_core_standalone.py) | 5 passed, 2 skipped |

## Threat Flags

None — all DDL uses static SQL or values derived from Python date objects (digits/dashes only). No user-controlled interpolation. T-17-04 and T-17-06 mitigations applied (static SQL + vendor guard).

## Self-Check: PASSED

| Item | Status |
|------|--------|
| src/sqlery/fastapi_sqlery/database.py | FOUND |
| src/sqlery/tables.py | FOUND |
| alembic/versions/20260612_0016_partition_queued_job.py | FOUND |
| commit d3ea683 (Task 1) | FOUND |
| commit 36fd347 (Task 2) | FOUND |
