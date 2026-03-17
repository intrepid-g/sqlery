# SQLery Issue Tracker

This document consolidates all tracked issues, TODOs, and known bugs for the SQLery project. Each item is enumerated for easy reference and feedback.

---
"[ ]" Feedback meaning:
[!] shoould be done (priority items)
[Q] should be done later ("Queue")
[NTH] make it "Nice to have"
[ ] still undecided whether to do it or not. 


## 📋 TODO Items (45 items)

### Priority: High (4 items)

#### FastAPI/Standalone Mode Compatibility (IN PROGRESS)
- [ ] **SQ-1**: Verify Queue API works in standalone/FastAPI mode
- [ ] **SQ-2**: Test `get_queue()` and Queue class in standalone mode
- [ ] **SQ-3**: Ensure all recent features (termination_reason, signal handling) work in standalone mode
- [ ] **SQ-4**: Add standalone mode examples to README
- [ ] **SQ-?**: Add README_FASTAPI.md and README_CLI.md with appropriate READMEs

### Priority: Medium (24 items)

#### Testing (3 items)
- [ ] **SQ-5**: Add tests for Queue API
  - [ ] **SQ-5a**: Test `get_queue()` function
  - [ ] **SQ-5b**: Test Queue class enqueue() method
  - [ ] **SQ-5c**: Test Queue class enqueue_at() method
  - [ ] **SQ-5d**: Test queue-specific defaults
  - [ ] **SQ-5e**: Test per-job overrides of queue defaults
  - [ ] **SQ-5f**: Test in both Django and standalone modes
- [ ] **SQ-6**: Add tests for termination_reason field
- [ ] **SQ-7**: Add tests for signal handling (SIGTERM, SIGKILL, SIGALRM)

#### Worker Management Commands (6 items)
- [x] **SQ-8**: `sqlery workers list` - List active workers
  - ✅ Already implemented in core/cli.py
  - Shows worker ID, node, PID, status, heartbeat, jobs processed
  - Supports --active-only/--all flag
  - Status: COMPLETED

- [x] **SQ-9**: `sqlery workers stop` - Stop workers gracefully
  - ✅ Implemented in core/cli.py
  - Sends SIGTERM for graceful shutdown
  - Worker finishes current job before exiting
  - Supports --worker-id to stop specific worker
  - Shows summary of stopped/failed workers
  - Status: COMPLETED (2025-11-12)

- [x] **SQ-10**: `sqlery workers kill` - Force kill workers
  - ✅ Implemented in core/cli.py
  - Sends SIGKILL for immediate termination
  - Requires --force flag for safety
  - Supports --worker-id to kill specific worker
  - ⚠️ WARNING: May leave jobs incomplete
  - Status: COMPLETED (2025-11-12)

- [x] **SQ-11**: `sqlery workers cleanup` - Clean up stale workers
  - ✅ Implemented in core/cli.py
  - Removes stale worker records from database
  - Configurable --max-age-hours (default: 1 hour)
  - Supports --dry-run to preview deletions
  - Does not touch running processes
  - Status: COMPLETED (2025-11-12)

- [ ] **SQ-12**: Verify worker heartbeat tracking
- [ ] **SQ-13**: Verify worker dashboard integration

#### Job Registry Implementation (8 items)
- [!] **SQ-14**: StartedRegistry - Running jobs
- [!] **SQ-15**: FinishedRegistry - Completed jobs
- [!] **SQ-16**: FailedRegistry - Failed jobs with error details
- [!] **SQ-17**: ScheduledRegistry - Delayed/scheduled jobs
- [!] **SQ-18**: DeferredRegistry - Jobs waiting for dependencies
- [!] **SQ-19**: CanceledRegistry - Canceled jobs
- [!] **SQ-20**: Automatic registry updates on state transitions
- [!] **SQ-21**: Configurable retention policies per registry

#### Database Retention & Cleanup (7 items)
- [Q] **SQ-22**: Age-based retention (delete jobs older than N days)
- [Q] **SQ-23**: Count-based retention (keep only N most recent jobs)
- [ ] **SQ-24**: Per-status retention policies
- [Q] **SQ-25**: Automatic cleanup in daemon mode
- [ ] **SQ-26**: Manual cleanup commands with dry-run support
- [ ] **SQ-27**: Database statistics command
- [Q] **SQ-28**: PostgreSQL VACUUM support

### Priority: Low (17 items)

#### Documentation (6 items)
- [ ] **SQ-29**: Add more real-world examples to README
- [!] **SQ-30**: Create quickstart guide
- [ ] **SQ-31**: Document production deployment best practices
- [ ] **SQ-32**: Add troubleshooting guide
- [ ] **SQ-33**: Document performance tuning tips
- [ ] **SQ-34**: Add architecture diagrams

#### Performance & Optimization (4 items)
- [ ] **SQ-35**: Benchmark Queue API performance
- [x] **SQ-36**: Optimize job claiming query ✅ FIXED (2025-01-25)
  - Added composite index `idx_jobs_claiming` on (status, queue_name, priority DESC, created_at ASC)
  - Job claiming now O(log n) instead of O(n)
- [x] **SQ-37**: Add database indexes for common queries ✅ FIXED (2025-01-25)
  - Added 11 common indexes for all hot paths
  - Added 4 PostgreSQL-specific partial indexes
  - Indexes: job claiming, scheduled jobs, worker lookup, cleanup, task status, registry
  - Code: `src/sqlery/schema.py` (create_indexes_sync, create_indexes_async)
  - Tests: `tests/test_schema_indexes.py` (16 tests)
- [ ] **SQ-38**: Profile memory usage

#### Nice-to-Have Features (7 items)
- [Q] **SQ-39**: Job dependencies (job A must complete before job B)
- [Q] **SQ-40**: Job chains (job A → job B → job C)
- [Q] **SQ-41**: Job result storage
- [Q] **SQ-42**: Webhook callbacks on job completion
- [ ] **SQ-43**: Metrics/monitoring integration (Prometheus, etc.)
- [Q] **SQ-44**: Job progress tracking
- [Q] **SQ-45**: Bulk job operations

### Priority: High - New Features (1 item)

#### Database Compatibility
- [x] **SQ-65**: 100% SQLite + PostgreSQL compatibility with dual locking mechanisms
  - ✅ Implemented database-agnostic job locking (SELECT FOR UPDATE SKIP LOCKED for Postgres, version-based for SQLite)
  - ✅ Added version field (optimistic locking) for 100% reliable atomic claiming
  - ✅ Ensure atomic job claiming works on both databases
  - ✅ Added comprehensive tests for both backends
  - ✅ Fixed test isolation issues (temp files per test)
  - ✅ Fixed timezone-aware datetime handling for SQLite
  - ✅ Fixed boolean type handling (SQLite uses integers)
  - ✅ **100% test pass rate achieved**: 49/49 tests passing (25 sync + 24 async)
  - ⚠️ RDS Data API scaffolding added but not integrated (see SQ-66)
  - Code: `sqlery/backends/sync_backend.py`, `sqlery/backends/async_backend.py`, `tests/backends/`
  - Docs: `SQLITE_POSTGRESQL_100_PERCENT_COMPATIBILITY.md`
  - Status: **TRULY COMPLETED** (2025-11-12) - Full production-ready compatibility

- [ ] **SQ-66**: Complete RDS Data API integration for Aurora Serverless
  - Wire up boto3 RDS Data API client to job claiming logic
  - Replace Django ORM queries with raw SQL for RDS Data API
  - Add settings: USE_RDS_DATA_API, RDS_DATA_API_CLUSTER_ARN, RDS_DATA_API_SECRET_ARN, RDS_DATA_API_DATABASE
  - Document configuration in CONFIGURATION.md
  - Add tests for RDS Data API mode
  - Code: `sqlery/db_compat.py`, `sqlery/worker_claiming.py`
  - Depends on: SQ-65 (completed)
  - Estimated effort: 6-8 hours

- [ ] **SQ-67**: Optimize tag locking performance for SQLite multi-worker deployments
  - Current: SQLite database-level locking serializes ALL workers (even for different tags)
  - PostgreSQL: Row-level locking allows parallel processing of jobs with different tags
  - Issue: Worker checking "stripe-api" blocks worker checking "acme-api" (unnecessary serialization)
  - Potential solutions:
    - Option A: Skip tag locking checks on SQLite (document as limitation - faster but less safe)
    - Option B: Implement application-level tag locking with UPDATE-based claiming on TagLock rows
    - Option C: Use separate SQLite database file per tag (complex setup)
    - Option D: Document current behavior as acceptable trade-off for SQLite
  - Note: Correctness is NOT affected (tag limits are enforced), only throughput
  - Code: `sqlery/worker_claiming.py:374-377`
  - Depends on: SQ-65 (completed)
  - Priority: Medium (performance optimization, not correctness issue)
  - Estimated effort: 4-6 hours (depends on chosen approach)

---

## 🐛 Known Bugs (19 items: SQ-46 through SQ-64)

### Scheduling (Cron, `ScheduledTask`) - 3 bugs
- [x] **SQ-46**: Missing recompute on cron change or re-enable
  - `next_run_at` is only set when absent in `ScheduledTask.save()` and not recalculated when `cron_expression` changes or `enabled` toggles back to True. This can leave stale schedules.
  - Status: **PARTIALLY FIXED** (2025-11-12)
    - ✅ Django mode: Already fixed in `ScheduledTask.save()` (lines 85-122)
    - ✅ Standalone mode: Fixed by adding `backend.update_scheduled_task()` method
    - Sub-issues created: SQ-46/1, SQ-46/2, SQ-46/3
  - Code:
    - Django: `sqlery/models.py` (`ScheduledTask.save`)
    - Standalone: `sqlery/backends/sync_backend.py`, `sqlery/backends/async_backend.py`

  - [ ] **SQ-46/1**: No tests for update_scheduled_task() method
    - Priority: HIGH
    - The new `update_scheduled_task()` method has zero test coverage
    - Need tests for:
      - Cron expression change → recalculates next_run_at
      - Re-enabling (False→True) → recalculates next_run_at
      - Disabling (True→False) → keeps next_run_at unchanged
      - Other field updates work correctly
      - No changes → returns current task unchanged
    - Code: `tests/backends/test_sync_backend.py`, `tests/backends/test_async_backend.py`

  - [ ] **SQ-46/2**: API inconsistency between Django and Standalone modes
    - Priority: MEDIUM
    - Django: Uses ORM `task.save()` with automatic recomputation
    - Standalone: Must manually call `backend.update_scheduled_task(task_id, ...)`
    - Different APIs for same functionality makes migration harder
    - Recommendation: Document the difference OR create unified high-level API
    - Code: Documentation needed in README or migration guide

  - [ ] **SQ-46/3**: Dynamic SQL construction in update_scheduled_task()
    - Priority: LOW
    - Uses f-strings to build SET clause dynamically
    - SQL injection not possible (keys are controlled), but not ideal pattern
    - Consider using query builder or ORM patterns
    - Code: `sqlery/backends/sync_backend.py:682-687`, `sqlery/backends/async_backend.py:682-687`

  - [ ] **SQ-55/1**: JobWrapper instances not picklable
    - Priority: MEDIUM
    - Parent: SQ-55 (fixed wraps issue, but pickling still broken)
    - JobWrapper is a class instance, not the same object as module-level name
    - Pickling fails with: `Can't pickle <function>: it's not the same object as module.name`
    - Would require implementing `__reduce__` or `__getstate__`/`__setstate__` for full pickle support
    - Impact: Limits use with multiprocessing, distributed task queues
    - Tests: Documented in `tests/test_sq55_functools_wraps.py:99-124` (tests expect PicklingError)
    - Code: `src/sqlery/core/job.py` (JobWrapper class)

- [!] **SQ-48**: Concurrency policy for scheduled tasks is rigid
  - New scheduled runs are skipped if any job for the task is `queued` or `running`, including a queued retry from a prior failure. This can suppress future schedule occurrences unintentionally.
  - Code: `sqlery/executor.py` (filter on `scheduled_task` with status in queued/running)

- [!] **SQ-49**: Next run calculation may skip occurrences
  - `next_run_at` is recalculated from `timezone.now()` rather than the prior schedule time, potentially skipping multiple missed intervals after downtime. Might (it IS intended) be intended but worth documenting.
  - Code: `sqlery/executor.py` (`calculate_next_run(..., base_time=timezone.now())`)

### Queueing and Execution - 2 bugs
- [ ] **SQ-51**: Large outputs can bloat rows
  - `mark_success()` and `mark_failed()` set full `output`/`error`/`traceback` text fields without size limits. Only the historical `runs` entries are truncated to 1000 chars.
  - Code: `sqlery/models.py` (`mark_success`, `mark_failed`, `_record_run`)

- [Q] **SQ-52**: `last_run_at` semantics incorrect
  - Help text says "Last successful execution time", but on success it's set to `started_at` rather than `finished_at`.
  - Code: `sqlery/executor.py` (after success)

### Retries - 2 bugs
- [!] **SQ-53**: History duplication across jobs
  - Retry jobs inherit the `runs` history from the failed job. This is by design (to keep a full attempt history on the active job), but it means the same history is stored on multiple rows, increasing storage. (maybe add a pointer to the failed job)
  - Code: `sqlery/executor.py` (`_retry_job`)

- [ ] **SQ-54**: Scheduling retried jobs uses wall-clock now
  - Delay is based on `timezone.now()`, not the actual failure time recorded in `finished_at`. Usually equivalent, but deviations are possible under clock skew.

### @job Decorator / DX - 2 bugs
- [x] **SQ-55**: Unusual use of `functools.wraps` on an instance ✅ FIXED
  - **Status**: Fixed - Changed from `wraps(func)(self)` to `functools.update_wrapper(self, func)`
  - **File**: `src/sqlery/core/job.py:50` (JobWrapper.__init__)
  - **Fix**: Replaced non-standard `wraps(func)(self)` with `functools.update_wrapper(self, func)`, which is the documented pattern for class-based wrappers
  - **Tests**: Added comprehensive test suite in `tests/test_sq55_functools_wraps.py` (18 tests)
  - **Coverage**: Tests metadata preservation (__name__, __doc__, __module__, __qualname__, __annotations__), introspection (inspect.signature, inspect.getsource, help()), and __wrapped__ attribute
  - **Sub-issue**: Created SQ-55/1 for pickling support (known limitation)

- [NTH] **SQ-56**: No support for positional args in queued jobs
  - Only `kwargs` are persisted; positional args aren't supported. This is fine if documented, but some devs may expect `args` support similar to Celery.
  - Code: `sqlery/models.py` (`kwargs` only), APIs

### Triggering (Middleware, HTTP mode) - 5 bugs
- [ ] **SQ-57**: HTTP client optional dep imported at module import
  - `httpx` is imported at the top of `http_trigger_middleware.py`. If users configure `TRIGGER_MODE!='http'` and never add this middleware, it's fine; however, importing the module without `httpx` installed will raise.
  - Code: `sqlery/http_trigger_middleware.py` (top-level import)

- [ ] **SQ-58**: Throttle cache is per-cache backend
  - If using `LocMemCache` across multiple worker processes, throttling won't be shared. Document or require a shared cache in production.
  - Code: `sqlery/middleware.py`, `http_trigger_middleware.py` (use of `cache`)

- [Q] **SQ-60**: Tight HTTP timeout
  - `httpx.Client(timeout=2.0)` may be too strict on slower setups; consider retry/backoff or a slightly higher timeout since the endpoint returns quickly but network conditions vary. make it configurable
  - Code: `sqlery/http_trigger_middleware.py`

- [ ] **SQ-61**: Signature skew sensitivity
  - Default `SIGNATURE_MAX_AGE=5` seconds can be brittle in multi-node deployments with clock skew. Consider documenting NTP requirement or increasing default.
  - Code: `sqlery/signature.py`, settings defaults

- [Q] **SQ-62**: Internal endpoints exposure
  - `/_internal/health` is unauthenticated. It returns only limited info, but some environments may want to restrict it.
  - Code: `sqlery/views.py` (`health_check`)

### API / Settings - 1 bug
- [NTH] **SQ-64**: Single interval for scheduler and workers
  - Middleware uses the same `CHECK_INTERVAL_SECONDS` for scheduler and worker triggers. Separate intervals may be useful.
  - Code: `sqlery/middleware.py`

---

## ✅ Fixed Issues (SQ-36, SQ-37, SQ-47, SQ-50, SQ-59, SQ-63)

### Fixed in v3.0.1
- ✅ **SQ-36**: Missing database indexes for job claiming query
  - Was: Job claiming query performed full table scan O(n)
  - Fix: Added composite indexes for (status, queue_name, scheduled_at, priority) pattern
  - Code: `sqlery/schema.py` (`INDEXES_SQL`, `create_indexes_sync/async`)

- ✅ **SQ-37**: Missing indexes for common query patterns
  - Was: Queries for stats, cleanup, and monitoring were slow
  - Fix: Added 11 common indexes + 4 PostgreSQL partial indexes for high-performance queries
  - Code: `sqlery/schema.py` (`INDEXES_SQL`, `INDEXES_SQL_POSTGRESQL_PARTIAL`)

- ✅ **SQ-63**: `AUTO_TRIGGER_WORKER` is unimplemented
  - Was: `_trigger_worker_if_needed()` was a placeholder and never triggered
  - Fix: Implemented subprocess-based worker trigger when `auto_trigger_worker=True`
  - Code: `sqlery/core/queue.py` (`_trigger_worker_if_needed`, `Queue.configure`)

### Fixed in v0.6.1
- ✅ **SQ-47**: Race when enqueueing from scheduler
  - Was: Two concurrent schedulers could both enqueue duplicate jobs for same scheduled task
  - Fix: Implemented atomic task claiming with `SELECT FOR UPDATE SKIP LOCKED` in `run_due_tasks()`
  - Code: `sqlery/executor.py` (`run_due_tasks`, `_enqueue_for_scheduled_task`)
  - Tests: `tests/test_atomic_scheduler.py`

### Fixed in v0.6.2
- ✅ **SQ-59**: Fragile subprocess resolution
  - Was: Subprocess commands used relative path `manage.py`, breaking when CWD ≠ project root
  - Fix: Created `get_manage_py_path()` helper that computes absolute path from `settings.BASE_DIR`
  - Works in Docker, systemd, cloud functions, and all deployment scenarios
  - Code: `sqlery/subprocess_executor.py` (`get_manage_py_path`), `views.py`, tests
  - Tests: `tests/test_subprocess.py`

### Fixed in v0.5.1
- ✅ **SQ-50**: Non-atomic job claiming
  - Was: Two workers could concurrently claim and execute the same job
  - Fix: Implemented `SELECT FOR UPDATE SKIP LOCKED` in `get_queued_jobs()` with atomic transactions
  - Code: `sqlery/executor.py` (`get_queued_jobs`, `run_queue_workers`, `execute_job`)
  - Tests: `tests/test_atomic_claiming.py`

---

## 📊 Summary Statistics

- **Total Items Tracked**: 70 (SQ-1 through SQ-67)

- **TODO Items**: 48 (SQ-1 through SQ-45, SQ-65, SQ-66, SQ-67)
  - High Priority: 6 (including SQ-65 ✅ **TRULY** completed 2025-11-12, SQ-66 pending)
  - Medium Priority: 25 (including SQ-67)
  - Low Priority: 17

- **Completed TODO Items**: 7 (SQ-8, SQ-9, SQ-10, SQ-11, SQ-36, SQ-37, SQ-65)

- **Known Bugs**: 18 fixed/partially fixed, 3 new sub-issues (SQ-46 through SQ-64, plus SQ-46/1 through SQ-46/3)
  - Scheduling: 3
  - Queueing: 2
  - Retries: 2
  - Decorator/DX: 2
  - Triggering: 5
  - API/Settings: 2

- **Fixed Issues**: 3 (SQ-47, SQ-50, SQ-59)

---

## 🔄 How to Use This Tracker

1. **Reference items by ID**: Use SQ-# for all items (TODOs, bugs, fixed issues)
2. **Mark completed**: Change `[ ]` to `[x]` when done
3. **Provide feedback**: Comment on specific SQ-# IDs in pull requests or issues
4. **Track progress**: This document is the single source of truth for all tracked work
5. **Continuous numbering**: New items continue from SQ-68 onwards

---

*Last updated: 2025-01-25*
*Version: 0.8.0 (Alpha)*

---

## 📝 Recent Updates

### 2025-01-25
- ✅ **SQ-36 COMPLETED**: Optimize job claiming query
  - Added composite index `idx_jobs_claiming` on (status, queue_name, priority DESC, created_at ASC)
  - Job claiming performance improved from O(n) to O(log n)

- ✅ **SQ-37 COMPLETED**: Add database indexes for common queries
  - Added 11 common indexes that work on both SQLite and PostgreSQL
  - Added 4 PostgreSQL-specific partial indexes for additional optimization
  - Indexes cover: job claiming, scheduled jobs, worker lookup, cleanup, task status, registry
  - Automatic index creation when calling `create_tables_sync()` or `create_tables_async()`
  - New standalone functions: `create_indexes_sync()` and `create_indexes_async()`
  - Registry table also added to schema (was missing from `schema.py`)
  - Code: `src/sqlery/schema.py`
  - Tests: `tests/test_schema_indexes.py` (16 comprehensive tests)

### 2025-11-12
- ✅ **SQ-46 PARTIALLY FIXED**: Fixed missing recompute on cron change or re-enable
  - Added `backend.update_scheduled_task()` method for standalone mode
  - Automatically recalculates next_run_at when:
    - Cron expression changes
    - Task is re-enabled (False → True)
  - Django mode was already fixed in `ScheduledTask.save()`
  - Created 3 sub-issues for follow-up (SQ-46/1, SQ-46/2, SQ-46/3):
    - SQ-46/1: Need tests for new method (HIGH priority)
    - SQ-46/2: API inconsistency between Django/Standalone (MEDIUM priority)
    - SQ-46/3: Dynamic SQL construction pattern (LOW priority)
  - Code: `src/sqlery/backends/sync_backend.py`, `src/sqlery/backends/async_backend.py`

- ✅ **SQ-8 to SQ-11 COMPLETED**: Implemented worker management CLI commands
  - `sqlery workers list` - List active workers with details
  - `sqlery workers stop` - Graceful shutdown via SIGTERM (finishes current job)
  - `sqlery workers kill --force` - Force kill via SIGKILL (immediate termination)
  - `sqlery workers cleanup` - Remove stale worker database records
  - Full signal handling support (SIGINT, SIGTERM) for graceful shutdown
  - Code: `src/sqlery/core/cli.py`

- ✅ **SQ-65 TRULY COMPLETED**: Achieved 100% test pass rate (49/49 tests) for SQLite/PostgreSQL compatibility
  - Fixed test isolation with temp file databases
  - Fixed timezone-aware datetime handling
  - Fixed boolean type compatibility
  - Both sync and async backends at 100%
  - Created comprehensive documentation: `SQLITE_POSTGRESQL_100_PERCENT_COMPATIBILITY.md`

### 2025-10-29
- ⚠️ **SQ-65 PARTIALLY COMPLETED**: Implemented version-based optimistic locking (but tests were failing)
- Added **SQ-66**: RDS Data API integration (scaffolding exists, needs wiring)
- Added **SQ-67**: Optimize tag locking performance for SQLite (currently correct but serializes all workers)
