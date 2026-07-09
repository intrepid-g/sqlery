---
phase: 17-fastapi-parity
reviewed: 2026-06-12T00:00:00Z
depth: deep
files_reviewed: 6
files_reviewed_list:
  - src/sqlery/core/models.py
  - src/sqlery/fastapi_sqlery/config.py
  - src/sqlery/fastapi_sqlery/database.py
  - src/sqlery/fastapi_sqlery/backend.py
  - src/sqlery/fastapi_sqlery/async_backend.py
  - alembic/versions/20260612_0016_partition_queued_job.py
findings:
  critical: 3
  warning: 3
  info: 2
  total: 8
status: issues_found
---

# Phase 17: Code Review Report

**Reviewed:** 2026-06-12
**Depth:** deep (cross-file, call-chain tracing)
**Files Reviewed:** 6
**Status:** issues_found

## Summary

The partition-parity implementation is structurally sound: DDL columns match
the SQLModel definition exactly (37 fields, confirmed by diff), `_partitioned_pg`
correctly uses `:name` named binds (Bug-SA-01 fix verified), the before_flush
listener is idempotent and thread-safe, the composite PK `(created_at, id)` is
present in both the DDL and the Django-mirror migration, and the inline cutover
path in `database.py` mirrors Django 0030 step-by-step. The Alembic 0016 revision
chains correctly off `20260608_0015`, is vendor-guarded, and includes a rollback.

Three blockers were found: a crash-on-use `NameError` in the SQLite cleanup dry-run
path, a silent config key mismatch that causes user-configured `PARTITION_RETENTION`
to be ignored in standalone mode, and `VACUUM ANALYZE` issued inside a SQLAlchemy
session (which wraps in a transaction), causing PostgreSQL to raise an error. Two
warnings and two info items follow.

---

## Critical Issues

### CR-01: `NameError: cutoff` in `cleanup_jobs` dry_run path

**File:** `src/sqlery/fastapi_sqlery/backend.py:924`

**Issue:** In the SQLite/non-partitioned-PG branch of `cleanup_jobs`, the
`dry_run=True` path (lines 915-926) uses the variable `cutoff` at line 924 inside
`if max_age_days:`, but `cutoff` is only defined at line 946 in the non-dry-run path.
Any caller that invokes `cleanup_jobs(max_age_days=N, dry_run=True)` raises
`NameError: name 'cutoff' is not defined`.

```python
# Current (broken):
if dry_run:
    count_stmt = select(func.count(QueuedJob.id))
    if max_age_days:
        count_stmt = count_stmt.where(QueuedJob.created_at < cutoff)  # NameError
    ...

# Fix: compute cutoff at the top of the SQLite branch before the dry_run fork:
cutoff = None
if max_age_days:
    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)

if dry_run:
    count_stmt = select(func.count(QueuedJob.id))
    if max_age_days and cutoff is not None:
        count_stmt = count_stmt.where(QueuedJob.created_at < cutoff)
    ...
```

---

### CR-02: `PARTITION_RETENTION` config key mismatch — user config silently ignored in standalone mode

**File:** `src/sqlery/fastapi_sqlery/backend.py:867` and `src/sqlery/core/daemon.py:478`

**Issue:** `StandaloneConfig` stores the partition retention setting under the key
`"PARTITION_RETENTION"` (an integer, number of months — `config.py:96`). However,
`backend.py` line 867 and `daemon.py` line 478 both call
`get_config("SQLERY_PARTITION_RETENTION", "30 days")` — the `SQLERY_` prefix means
the lookup misses the stored key, returns the fallback default `"30 days"`, and the
user-configured value is silently discarded on every call.

The same mismatch exists for `SQLERY_PARTITION_INTERVAL`, `SQLERY_PARTITION_PREMAKE`,
and `SQLERY_PARTITION_ARCHIVE_HOOK` in `daemon.py` (lines 477, 479, 480) vs the
keys `PARTITION_INTERVAL`, `PARTITION_PREMAKE`, `PARTITION_ARCHIVE_HOOK` stored in
`StandaloneConfig`.

There is also a type mismatch: `config.py` stores `PARTITION_RETENTION` as an
integer (months), while `daemon.py` and `backend.py` both expect a PostgreSQL
interval string like `"30 days"` to pass directly to
`reclaim_drained_partitions(cur, table, retention_str, ...)`. If the key mismatch
were fixed without also normalising the type, the integer `24` would be passed
as `retention_str` and `cur.execute("SELECT now() - %s::interval", [24])` would
fail at the PostgreSQL level.

This is a correctness failure in standalone mode: the configured retention is never
applied; reclaim always uses the hardcoded 30-day fallback regardless of what
`PARTITION_RETENTION` is set to.

**Fix:** Either (a) rename the stored keys in `StandaloneConfig` to use the
`SQLERY_` prefix to match what callers request, and convert the integer to a string
at read time, or (b) convert at the call sites:

```python
# Option A — in StandaloneConfig._config, rename and store as string:
'SQLERY_PARTITION_RETENTION': '24 months',   # was 'PARTITION_RETENTION': 24

# Option B — at the backend.py call site:
_ret = get_config("PARTITION_RETENTION", 24)  # use the correct key
retention_str = f"{_ret} months" if isinstance(_ret, int) else _ret
```

The `_validate_partition_config` method and `_PARTITION_KEYS` set in `config.py`
must be updated to match whichever key naming scheme is chosen.

---

### CR-03: `VACUUM ANALYZE` inside a SQLAlchemy session raises an error on PostgreSQL

**File:** `src/sqlery/fastapi_sqlery/backend.py:1083-1092`

**Issue:** `vacuum_database` issues `VACUUM ANALYZE` statements via
`session.exec(text("VACUUM ANALYZE ..."))` inside a `get_session()` context manager.
SQLAlchemy's `Session` operates in a transaction (autocommit is off by default).
PostgreSQL does not allow `VACUUM` inside a transaction block and raises:
`ERROR: VACUUM cannot run inside a transaction block`. The `try/except` in
`vacuum_database` catches this and returns `{"success": False, "error": ...}`,
making the vacuum permanently silently fail on every call.

The Django backend avoids this by using `connection.cursor()` which runs outside the
ORM transaction.

```python
# Fix: use engine.connect() with execution_options(isolation_level="AUTOCOMMIT")
# instead of get_session():
from .database import get_engine
engine = get_engine()
with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
    if not self._partitioned_pg():
        conn.execute(text("VACUUM ANALYZE sqlery_queued_job"))
    conn.execute(text("VACUUM ANALYZE sqlery_scheduled_task"))
    conn.execute(text("VACUUM ANALYZE sqlery_registry"))
    conn.execute(text("VACUUM ANALYZE sqlery_worker"))
```

---

## Warnings

### WR-01: `downgrade()` Step 4 — rename is not idempotent

**File:** `alembic/versions/20260612_0016_partition_queued_job.py:303-306`

**Issue:** Step 4 of `downgrade()` executes a bare `ALTER TABLE
sqlery_queued_job_unpartitioned RENAME TO sqlery_queued_job;` without an existence
guard. Steps 1-3 all have `IF NOT EXISTS` guards or `DO $$ BEGIN IF ... END $$;`
guards for re-run safety. If the migration runner crashes between Step 4's rename
and recording the new head (or if `downgrade()` is re-invoked), the rename fails
because `sqlery_queued_job_unpartitioned` no longer exists (it was already renamed
to `sqlery_queued_job`). This leaves the DB in a state that cannot complete the
rollback without manual intervention.

```sql
-- Fix: wrap step 4 in an existence guard (mirrors the guard in step 3):
DO $$ BEGIN
    IF to_regclass('public.sqlery_queued_job_unpartitioned') IS NOT NULL
       AND to_regclass('public.sqlery_queued_job') IS NULL THEN
        ALTER TABLE sqlery_queued_job_unpartitioned RENAME TO sqlery_queued_job;
    END IF;
END $$;
```

---

### WR-02: `upgrade()` S6 creates index without `IF NOT EXISTS` — not re-run safe after partial failure

**File:** `alembic/versions/20260612_0016_partition_queued_job.py:197-200`

**Issue:** S6 drops `sqlery_job_pending_idx` first (`DROP INDEX IF EXISTS`) then
creates it without `IF NOT EXISTS`. The comment explains this is intentional because
`IF NOT EXISTS` would find the old name on the legacy table and silently skip
creation on the partitioned parent. This logic is correct for the normal execution
path.

However, if the migration crashes between S6 (index created) and the end of S9
(sequence seeded), and is re-run, S7 creates partitions again (`CREATE TABLE IF NOT
EXISTS` — idempotent), S8 bulk-copies again (`ON CONFLICT DO NOTHING` — idempotent),
but S6 will have already run its `DROP INDEX IF EXISTS` (no-op, index is on the new
partitioned table now, not legacy) and then `CREATE INDEX` without `IF NOT EXISTS`
will fail with `already exists`.

```sql
-- Fix: add IF NOT EXISTS to the S6 CREATE INDEX (the concern about finding the name
-- on legacy only applies during the first run; after S1 the legacy table is renamed
-- and the index moves to it, so IF NOT EXISTS on re-run finds it on the partitioned table):
-- Actually: after a crash post-S6, the index IS on the new table. IF NOT EXISTS is safe.
CREATE INDEX IF NOT EXISTS sqlery_job_pending_idx
    ON sqlery_queued_job (queue_name, priority DESC, created_at)
    WHERE status = 'queued';
```

The guard can simply be added — on re-run, the DROP IF EXISTS on legacy is a no-op
(the legacy index is already gone from the first run), and `CREATE INDEX IF NOT
EXISTS` will correctly skip creation if the index already exists on the partitioned
table.

---

### WR-03: `get_raw_cursor()` does not close or return the underlying `raw_connection` if the cursor is closed independently

**File:** `src/sqlery/fastapi_sqlery/backend.py:117-134`

**Issue:** `get_raw_cursor()` calls `engine.raw_connection()` which checks out a
pooled connection, then returns only the cursor. The underlying `raw_conn` is not
stored. The cleanup code in `cleanup_jobs` correctly closes the cursor and then calls
`cur.connection.close()` to return the pooled connection. However, the docstring
says "CALLER OWNS THE CURSOR LIFECYCLE" and warns about wrapping in `try/finally`,
but the callers outside `cleanup_jobs` (daemon maintenance loop) may not implement
the `cur.connection.close()` call — only `cur.close()`. A caller that calls only
`cursor.close()` leaks the pool connection for the lifetime of the process.

The docstring warning is present but easy to miss. A safer API would return both
the cursor and the connection, or encapsulate the cleanup inside a context manager.

```python
# Safer alternative: return a context manager that owns both cursor and connection
from contextlib import contextmanager

@contextmanager
def raw_cursor(self):
    """Context manager that yields a psycopg cursor and cleans up on exit."""
    if not self._partitioned_pg():
        yield None
        return
    engine = get_engine()
    raw_conn = engine.raw_connection()
    cur = raw_conn.cursor()
    try:
        yield cur
    finally:
        cur.close()
        try:
            raw_conn.close()
        except Exception:
            pass
```

---

## Info

### IN-01: `QUEUED_JOB` imported in `database.py` but never used

**File:** `src/sqlery/fastapi_sqlery/database.py:15`

**Issue:** `from ..tables import QUEUED_JOB` is imported at the top of `database.py`
but is never referenced in the file. The string constant `"sqlery_queued_job"` is
used inline throughout `_init_partitioned_pg` and `_build_partitioned_jobs_ddl`
rather than through this import.

```python
# Remove the unused import:
# from ..tables import QUEUED_JOB   # unused — remove this line
```

---

### IN-02: `_validate_partition_config` maintenance interval upper bound is too permissive for 'weekly' interval

**File:** `src/sqlery/fastapi_sqlery/config.py:235-243`

**Issue:** The validator enforces `PARTITION_MAINTENANCE_INTERVAL_MINUTES <= 43200`
(30 days), which is documented as "one month". When `PARTITION_INTERVAL='weekly'`,
a maintenance interval of up to 43,200 minutes (30 days) is accepted — meaning
maintenance could run once every 30 days even though new weekly partitions must be
provisioned every 7 days. The `core/daemon.py` has a proper
`_validate_partition_maintenance_interval` function that checks against the actual
partition interval, but that is only run at daemon startup. A user who sets
`PARTITION_INTERVAL='weekly'` and `PARTITION_MAINTENANCE_INTERVAL_MINUTES=20000`
would pass `config.py` validation but fail daemon startup validation.

The `config.py` validator should check `maint_mins` against the partition interval
granularity directly:

```python
# Add after the `valid_intervals` check:
if interval == "weekly":
    max_maint_mins = 7 * 24 * 60  # 10080 minutes
elif interval == "monthly":
    max_maint_mins = 43200  # 30 days
if maint_mins > max_maint_mins:
    raise ValueError(
        f"PARTITION_MAINTENANCE_INTERVAL_MINUTES ({maint_mins}) must be "
        f"<= {max_maint_mins} for PARTITION_INTERVAL='{interval}'"
    )
```

---

## Sound areas (explicitly verified)

- **before_flush listener** (`core/models.py:454-465`): Correct. Fires only on
  `session.new` objects with `id is None`. `uuid7().int & ((1<<62)-1)` is a 62-bit
  positive integer, within signed BigInteger range. Registered on `_SASession` class —
  SQLAlchemy deduplicates class-level listeners, so re-import is safe. No shared
  mutable state; thread-safe.

- **`_partitioned_pg` fix (Bug-SA-01)** (`backend.py:93-98`, `async_backend.py:105-110`):
  Verified fixed. Both use `:name` named binds with `{"name": ...}` dict — correct
  for SQLAlchemy 2.x `text()`.

- **DDL column parity** (`database.py:_build_partitioned_jobs_ddl`): All 37 columns
  in the DDL exactly match the 37 SQLModel `QueuedJob` fields. No drift detected.
  `Worker` and `JobRegistry` raw-SQL tables also match their SQLModel definitions.

- **Composite PK ordering**: DDL and Django migration both use `PRIMARY KEY (created_at, id)`.
  The SQLModel definition has `id` declared first in Python (required for SQLAlchemy's
  mapper), but the physical PK is `(created_at, id)` as declared in the DDL. No
  correctness issue — partition pruning works on `created_at`.

- **SQL injection surface in partition DDL**: Date literals in `_init_partitioned_pg`
  and Alembic 0016 S7 are derived from `date.strftime()` / `datetime.strftime()`
  (digits and dashes only). Partition name suffixes are also date-derived. These are
  safe interpolations into f-strings; no user-controlled data enters the DDL.

- **Alembic 0016 chain**: `down_revision = '20260608_0015'` correctly chains after
  0015. Vendor guard (`bind.dialect.name != "postgresql"` → return) is the first
  check in both `upgrade()` and `downgrade()`. D6 compliance confirmed.

- **D6 (SQLite unchanged)**: `init_database` vendor-branches on `database_url.startswith('sqlite')`.
  SQLite takes `SQLModel.metadata.create_all(_engine)`. No partition DDL is emitted.

- **Write-path `created_at` pruning**: All write paths (`cancel_job`, `mark_job_archived`,
  `cascade_ancestor_status`, `release_claimed_job`, `update_job_child_pid`,
  `atomic_claim_job`) fetch `created_at` first and include it in the `WHERE` clause,
  matching the Django backend's approach.

- **`get_raw_cursor` closes raw connection in `cleanup_jobs`**: The `try/finally` block
  at lines 873-889 correctly calls `cur.close()` then `cur.connection.close()`,
  returning the pooled connection to the pool. This is more careful than the Django
  equivalent (which only calls `cur.close()`).

---

_Reviewed: 2026-06-12_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
