---
phase: 14-scheduled-job-staging
reviewed: 2026-06-11T00:00:00Z
depth: deep
files_reviewed: 6
files_reviewed_list:
  - src/sqlery/django_sqlery/models.py
  - src/sqlery/django_sqlery/migrations/0029_scheduled_job_staging.py
  - src/sqlery/django_sqlery/backend.py
  - src/sqlery/core/scheduler.py
  - src/sqlery/core/daemon.py
  - src/sqlery/django_sqlery/settings.py
findings:
  critical: 3
  warning: 4
  info: 2
  total: 9
status: issues_found
---

# Phase 14: Code Review Report

**Reviewed:** 2026-06-11
**Depth:** deep
**Files Reviewed:** 6
**Status:** issues_found

## Summary

Phase 14 implements far-future job staging (`sqlery_scheduled_job` table) with enqueue routing,
a promotion tick, and dual-table API coverage. The routing logic and dual-table API (get_job_by_id,
cancel_job) are structurally sound. However, three blockers were found: (1) `DELETE ... FOR UPDATE
SKIP LOCKED` is invalid PostgreSQL syntax and will cause a hard runtime error when Phase 16 wires
in the raw cursor; (2) the DELETE and INSERT are not wrapped in an explicit transaction, so a crash
after DELETE commits but before INSERT commits loses rows permanently; (3) the promotion tick is
nested inside the partition maintenance block, meaning it never runs on SQLite (jobs staged on
SQLite are stuck forever) and never runs when `PARTITION_MAINTENANCE_ENABLED=False`. Several
warnings cover field-fidelity loss through the staging path, the orphaned sequence object left by
the migration, and misleading "Enqueued" log messages when a job is actually staged.

---

## Critical Issues

### CR-01: `DELETE ... FOR UPDATE SKIP LOCKED` is invalid PostgreSQL syntax

**File:** `src/sqlery/core/scheduler.py:419`
**Issue:** PostgreSQL's `FOR UPDATE` / `FOR SHARE` locking clauses are part of the `SELECT`
statement grammar only. A bare `DELETE FROM t WHERE ... FOR UPDATE SKIP LOCKED RETURNING *` is a
syntax error that PostgreSQL will reject at parse time:
```
ERROR:  syntax error at or near "FOR"
```
The function is currently unreachable in production (because `backend.get_raw_cursor()` does not
exist until Phase 16), so no test has caught this. Once Phase 16 wires the cursor in, every
promotion tick will raise immediately, silently swallowed by the surrounding `except Exception`.

All mock-cursor tests pass because they never execute the SQL string against a real database.

**Fix:** Rewrite the DELETE as a CTE that uses `SELECT ... FOR UPDATE SKIP LOCKED` then
`DELETE WHERE id IN (...)`:
```sql
-- Step 2a: lock and delete due rows atomically
WITH locked AS (
    SELECT id
    FROM sqlery_scheduled_job
    WHERE scheduled_at <= now() + make_interval(secs => %s)
    FOR UPDATE SKIP LOCKED
)
DELETE FROM sqlery_scheduled_job
WHERE id IN (SELECT id FROM locked)
RETURNING id, queue_name, task_path, payload,
          scheduled_at, priority, max_retries, created_at
```
Pass `[_PROMOTION_LOOKAHEAD_SECONDS]` as the parameter. No other changes needed.

---

### CR-02: DELETE and INSERT are not in an explicit transaction — data loss on INSERT failure

**File:** `src/sqlery/core/scheduler.py:416`
**Issue:** The docstring claims "Single transaction: DELETE … RETURNING → INSERT" but the
function issues raw `cur.execute()` calls with no explicit `BEGIN` / `COMMIT`. The atomicity
depends entirely on the caller providing a cursor that is already inside an open transaction. The
docstring says `"autocommit or inside a transaction — caller's choice"`, which means callers
passing an autocommit cursor can silently lose rows: if `DELETE RETURNING` succeeds and commits,
then any `INSERT` in the loop fails (constraint violation, serialization error, mid-batch crash),
those rows are permanently deleted from `sqlery_scheduled_job` and never inserted into
`sqlery_queued_job`.

The daemon caller at `daemon.py:636` has no transaction wrapper around the call.

**Fix:** Wrap the DELETE + INSERT loop in an explicit transaction savepoint inside the function:
```python
# At the top of the try block, after the advisory lock is acquired:
cur.execute("BEGIN")          # or use a savepoint if already in a txn
try:
    cur.execute("WITH locked AS (...) DELETE ...")
    rows = cur.fetchall()
    if not rows:
        cur.execute("COMMIT")
        return 0
    for row in rows:
        (job_id, ...) = row
        cur.execute("INSERT INTO sqlery_queued_job ...", [...])
    cur.execute("COMMIT")
except Exception:
    cur.execute("ROLLBACK")
    raise
```
Alternatively, require the caller to always pass a cursor in a transaction and document this
contractually (updating the docstring from "autocommit or inside a transaction" to "must be inside
a transaction"). The daemon path in `daemon.py` must then open an explicit transaction before
calling `promote_due_scheduled_jobs`.

---

### CR-03: Promotion tick is gated inside partition-maintenance block — never runs on SQLite or when partition maintenance is disabled

**File:** `src/sqlery/core/daemon.py:607`
**Issue:** The call to `promote_due_scheduled_jobs` at line 636 is nested three levels deep inside:
```python
if partition_maintenance_enabled and _partition_maint_available and _should_run_partition_maintenance(...):
    try:
        cur = backend.get_raw_cursor()   # Phase 16 TODO: AttributeError until then
        ...
        promote_due_scheduled_jobs(cur)  # never reached currently
```
This means staging promotion does not run in any of these real-world configurations:
- **SQLite only** (`_PSYCOPG_AVAILABLE = False` → `_partition_maint_available = False`): far-future
  jobs are routed to `sqlery_scheduled_job` by `create_job` but promotion is permanently skipped.
  Staged SQLite jobs are stuck forever.
- **`PARTITION_MAINTENANCE_ENABLED=False`**: the entire block is skipped, promotion silently stops.
- **Currently, always**: `backend.get_raw_cursor()` raises `AttributeError` (noted as a Phase 16
  TODO). The outer `except Exception` catches this and logs "Partition maintenance error", making
  promotion failures invisible in logs unless the operator specifically looks for that message.

**Fix:** Decouple the promotion tick from partition maintenance. Add an independent promotion tick
that runs every daemon cycle regardless of partition maintenance state, guarded only by
`_PSYCOPG_AVAILABLE`:
```python
# After the partition maintenance block, add a standalone promotion tick:
if _PSYCOPG_AVAILABLE:
    try:
        cur = backend.get_raw_cursor()
        promoted = promote_due_scheduled_jobs(cur)
        if promoted > 0:
            logger.info(f"Promotion tick: promoted {promoted} staged job(s)")
    except AttributeError:
        # get_raw_cursor not yet wired (Phase 16)
        pass
    except Exception as promo_exc:
        logger.error(f"Promotion error: {promo_exc}", exc_info=True)
```
Additionally, add a SQLite-mode ORM-based promoter so the staging feature is functional in SQLite:
```python
else:
    # SQLite fallback: promote using Django ORM (no SKIP LOCKED needed; advisory lock is PG-only)
    from sqlery.django_sqlery.models import ScheduledJob, QueuedJob
    from django.utils import timezone as dj_tz
    from datetime import timedelta
    lookahead = dj_tz.now() + timedelta(seconds=_PROMOTION_LOOKAHEAD_SECONDS)
    due = ScheduledJob.objects.filter(scheduled_at__lte=lookahead)
    for sj in due:
        QueuedJob.objects.create(id=sj.id, task_path=sj.task_path, kwargs=sj.payload, ...)
        sj.delete()
```

---

## Warnings

### WR-01: Twelve job fields silently dropped when routing to staging table

**File:** `src/sqlery/django_sqlery/backend.py:86`
**Issue:** `create_job` accepts 22 parameters but `ScheduledJob.objects.create()` stores only 6:
`queue_name`, `task_path`, `payload` (=kwargs), `scheduled_at`, `priority`, `max_retries`. The
following parameters are silently dropped when a job is staged:

| Dropped field | Impact after promotion |
|---|---|
| `retry_backoff` | Promoted job uses DB default (1.0) — not user-specified value |
| `allow_parallel` | Promoted job uses DB default (False) — could change concurrency semantics |
| `timeout_seconds` | Promoted job has no timeout, even if one was requested |
| `job_name` | Uniqueness contract broken; two staged jobs with same `job_name` can coexist |
| `retry_intervals` | Fixed retry schedule lost; falls back to exponential backoff |
| `meta` | User metadata permanently lost |
| `dependencies` | Dependency graph lost; promoted job ignores dependencies |
| `on_success_path` / `on_failure_path` | Callbacks silently dropped |
| `ttl` / `result_ttl` / `failure_ttl` | TTL contracts silently dropped |
| `scheduled_task_id` | Promoted job is not linked to its scheduled task |

The caller has no way to know which fields will survive staging. A job with `dependencies=[5, 6]`
staged for 60 days will be promoted and execute immediately, ignoring its dependency chain.

**Fix:** Either (a) add the missing columns to `ScheduledJob` and carry them through promotion, or
(b) document the unsupported-fields list clearly in the `create_job` docstring and raise
`ValueError` at enqueue time if an unsupported field is provided with a non-default value when the
staging path will be taken:
```python
if scheduled_at is not None and scheduled_at > now_utc + staging_threshold:
    # Check for fields unsupported in staging
    unsupported = {}
    if dependencies:
        unsupported['dependencies'] = dependencies
    if job_name:
        unsupported['job_name'] = job_name
    # ... etc.
    if unsupported:
        raise ValueError(
            f"Far-future jobs (scheduled_at > threshold) do not support: {list(unsupported)}"
        )
```

---

### WR-02: Promoted row INSERT omits non-nullable JSON columns that have callable defaults

**File:** `src/sqlery/core/scheduler.py:439`
**Issue:** The raw `INSERT INTO sqlery_queued_job` names only 9 columns. Columns like `runs`,
`tags`, `dependencies`, `webhook_events` have `JSONField(default=list)`. Django sets a SQL-level
`DEFAULT '[]'` for these columns in PostgreSQL migrations, so the INSERT should succeed. However,
this is fragile: it relies on Django's migration having set those column defaults correctly at
schema creation time. Any column added by a future migration that uses a callable default but whose
`AddField` migration does not set a SQL-level default will silently fail at promotion time with a
`NOT NULL` violation.

The promoted row also gets `status = 'queued'` hard-coded in the SQL (correct), but `version`
relies on the DB default of `0`. If the `version` column ever loses its DEFAULT (e.g., after a
migration that recreates the column), the INSERT will fail.

**Fix:** Enumerate all required columns explicitly in the INSERT and supply their defaults
programmatically rather than relying on DB-level defaults:
```python
cur.execute(
    "INSERT INTO sqlery_queued_job"
    " (id, queue_name, task_path, kwargs, scheduled_at, priority, max_retries,"
    "  status, version, retry_count, retry_backoff, allow_parallel,"
    "  runs, tags, dependencies, webhook_events, output, error, traceback,"
    "  on_success_path, on_failure_path, termination_reason, created_at)"
    " VALUES (%s, %s, %s, %s, %s, %s, %s,"
    "  'queued', 0, 0, 1.0, false,"
    "  '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, '', '', '',"
    "  '', '', '', %s)",
    [job_id, queue_name, task_path, payload,
     scheduled_at, priority, max_retries, created_at],
)
```

---

### WR-03: Orphaned `sqlery_scheduled_job_id_seq` sequence after migration 0029

**File:** `src/sqlery/django_sqlery/migrations/0029_scheduled_job_staging.py:104`
**Issue:** `CreateModel` for `ScheduledJob` auto-creates `sqlery_scheduled_job_id_seq`. The
subsequent `_PgSequenceWiring` then issues:
```sql
ALTER SEQUENCE IF EXISTS sqlery_scheduled_job_id_seq OWNED BY NONE;
```
This detaches the sequence from the column but does **not** drop it. The old sequence remains in
the database indefinitely, orphaned. In test environments that run migrations repeatedly, this
accumulates. More importantly, `OWNED BY NONE` means the sequence will **not** be automatically
dropped if the table is dropped (e.g., when applying `migrate --run-syncdb` or rolling back the
migration manually before running the reverse).

The reverse migration also has a gap: after rolling back, the id column's `DEFAULT` is dropped
before re-creating the dedicated sequence. Any new insert between `DROP DEFAULT` and `CREATE
SEQUENCE` will fail.

**Fix:** Add a `DROP SEQUENCE IF EXISTS` to the forward SQL and reorder the reverse SQL:
```python
sql=[
    "ALTER SEQUENCE IF EXISTS sqlery_scheduled_job_id_seq OWNED BY NONE;",
    "DROP SEQUENCE IF EXISTS sqlery_scheduled_job_id_seq;",
    "ALTER TABLE sqlery_scheduled_job ALTER COLUMN id"
    " SET DEFAULT nextval('sqlery_queued_job_id_seq'::regclass);",
],
reverse_sql=[
    "CREATE SEQUENCE IF NOT EXISTS sqlery_scheduled_job_id_seq"
    " OWNED BY sqlery_scheduled_job.id;",
    "ALTER TABLE sqlery_scheduled_job ALTER COLUMN id"
    " SET DEFAULT nextval('sqlery_scheduled_job_id_seq'::regclass);",
],
```

---

### WR-04: `_validate_staging_config` is only called when partition maintenance is enabled and psycopg is available

**File:** `src/sqlery/core/daemon.py:486`
**Issue:** The staging config validation (`_validate_staging_config`) at line 497 is inside:
```python
if partition_maintenance_enabled and _partition_maint_available:
    ...
    _validate_staging_config(staging_threshold_days, partition_retention_str)
```
On SQLite (where `_PSYCOPG_AVAILABLE = False` → `_partition_maint_available = False`), the
validation is never executed. A misconfigured `SQLERY_PARTITION_RETENTION <= threshold` will go
undetected until the operator switches to PostgreSQL. Also, a string value in
`SQLERY_SCHEDULED_JOB_THRESHOLD_DAYS` (e.g., `"1"` from an environment variable) causes
`retention_days_value <= threshold_days` to raise `TypeError` (float compared to str) at
validation time, crashing the daemon startup on PostgreSQL.

**Fix:** Move `_validate_staging_config` to execute unconditionally at daemon startup,
independently of partition maintenance. Add a type coercion guard:
```python
# At daemon startup, before the main loop:
staging_threshold_days = get_config("SQLERY_SCHEDULED_JOB_THRESHOLD_DAYS", 1)
try:
    _validate_staging_config(int(staging_threshold_days), partition_retention_str)
except (ValueError, TypeError) as e:
    logger.error(f"Staging config error: {e}")
```

---

## Info

### IN-01: `mark_job_success` and `mark_job_failed` call `get_job_by_id` which can return `ScheduledJob`

**File:** `src/sqlery/django_sqlery/backend.py:687`
**Issue:** `get_job_by_id` now falls back to `ScheduledJob` when a job is not found in
`QueuedJob`. `mark_job_success(job_id)` and `mark_job_failed(job_id)` call `get_job_by_id` then
invoke `job.mark_success()` or `job.mark_failed()`. `ScheduledJob` has neither method; calling
either on a staged job raises `AttributeError`. In normal execution flow this path is never reached
(a staged job cannot be `'running'`), but it is exploitable via the admin UI or API if an operator
calls these methods directly by a job ID that belongs to a staged row.

**Fix:** Add a `isinstance` guard or a duck-type check:
```python
def mark_job_success(self, job_id: int, output: str = ""):
    job = self.get_job_by_id(job_id)
    if job and hasattr(job, "mark_success"):
        job.mark_success(output=output)
    return job
```

---

### IN-02: Misleading "Enqueued job X" log when job is actually staged

**File:** `src/sqlery/core/scheduler.py:166`
**Issue:** `_enqueue_for_scheduled_task` logs `"Enqueued job {job.id} for scheduled task..."` for
all `create_job` return values. If the scheduled task has a far-future `scheduled_at` (unlikely
given that cron tasks use `scheduled_at=None`, but possible with a once-type task), the returned
object is a `ScheduledJob`, not a `QueuedJob`. The log says the job is enqueued when it is
actually staged. This also applies to the non-cron branch at line 192.

**Fix:** Check the type and adjust the log message:
```python
from sqlery.django_sqlery.models import ScheduledJob as _ScheduledJob
verb = "Staged" if isinstance(job, _ScheduledJob) else "Enqueued"
logger.info(f"{verb} job {job.id} for scheduled task '{task.name}' ...")
```

---

_Reviewed: 2026-06-11_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
