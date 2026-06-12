# Plan 17-01 Summary: SQLModel composite PK + ScheduledJob + standalone partition config

**Status:** Complete. (Executor subagent ran against a stale worktree base and produced an unmergeable commit; the intended source changes were recovered via a 3-way patch onto current HEAD and re-verified by the orchestrator.)

## What was built (commit `2d7264b`)

### core/models.py
- `QueuedJob` now has a **composite primary key `(id, created_at)`** mirroring the Django model. `id` is `Column(BigInteger, primary_key=True, nullable=False)` (no per-table autoincrement — on PG it draws from the shared `sqlery_job_id_seq`, wired in 17-02's DDL). `created_at` is `primary_key=True`.
- SQLite has no autoincrement for composite PKs, so a `before_flush` SQLAlchemy event listener (`_assign_composite_pk_ids`) assigns a 62-bit UUID-v7-derived int to `QueuedJob`/`ScheduledJob` rows when `id is None`. On PG the server default supplies it, so the listener is a no-op there.
- New **`ScheduledJob`** staging SQLModel (`__tablename__='sqlery_scheduled_job'`), composite PK, slim fields mirroring Django's.

### fastapi_sqlery/config.py
- `StandaloneConfig` gains the 6 partition keys (`PARTITION_INTERVAL`, `PARTITION_PREMAKE`, `PARTITION_RETENTION`, `PARTITION_ARCHIVE_HOOK`, `PARTITION_MAINTENANCE_INTERVAL_MINUTES`, `SCHEDULED_JOB_THRESHOLD_DAYS`) with env-var loading and `_validate_partition_config()` enforcing the same invariants as Django (retention > threshold; maintenance interval ≤ partition interval; premake ≥ 1) — D1.

### fastapi_sqlery/backend.py
- All 13 `session.get(QueuedJob, id)` calls (broken by composite PK) replaced with `select(QueuedJob).where(QueuedJob.id == id).first()`. Old lines commented out per convention.

### tests
- 3 test-side `session.get(QueuedJob, j.id)` calls in `tests/unit/test_sqlalchemy_backend_sync.py` fixed the same way.

## Verification
- `import sqlery.core.models` OK; `QueuedJob` PK cols = `['id', 'created_at']`; `ScheduledJob` present.
- `tests/test_core_standalone.py` + `tests/unit/test_sqlalchemy_backend_sync.py`: **84 passed**, no new failures (pre-existing aiosqlite async errors are unrelated — missing driver).

## Note for downstream plans
17-02 (database.py PG partitioned DDL + Alembic) and 17-03 (backend wiring) build on this composite-PK model and the shared `sqlery_job_id_seq`.
