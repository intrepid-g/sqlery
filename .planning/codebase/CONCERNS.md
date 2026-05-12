# Codebase Concerns

**Analysis Date:** 2026-05-12

## Tech Debt

**24 backward-compatibility stub files at package root:**
- Issue: During a migration from flat `src/sqlery/` modules to `src/sqlery/django_sqlery/`, 24 stub files were left behind. Each contains `#CLEANUP` comments and re-exports from the new location. These stubs add confusion about where real code lives.
- Files: `src/sqlery/admin.py`, `src/sqlery/apps.py`, `src/sqlery/cleanup.py`, `src/sqlery/daemon_manager.py`, `src/sqlery/daemon_worker.py`, `src/sqlery/dashboard_views.py`, `src/sqlery/db_compat.py`, `src/sqlery/executor.py`, `src/sqlery/http_trigger_middleware.py`, `src/sqlery/middleware.py`, `src/sqlery/views.py`, `src/sqlery/worker_claiming.py`, `src/sqlery/worker_process.py`, `src/sqlery/worker_registry.py`, `src/sqlery/registries.py`, `src/sqlery/settings.py`, `src/sqlery/urls.py`, `src/sqlery/daemon_middleware.py`, `src/sqlery/subprocess_middleware.py`, `src/sqlery/subprocess_executor.py`, `src/sqlery/models.py`, `src/sqlery/rate_limit_utils.py`, `src/sqlery/worker.py`, `src/sqlery/decorators.py`
- Impact: Maintainers cannot tell at a glance which file to edit. New contributors import from wrong paths. Package size is inflated.
- Fix approach: Replace stubs with deprecation warnings, add `__all__` to `__init__.py` for public API, then remove stubs after one release cycle.

**Duplicate claiming algorithms (core vs django_sqlery):**
- Issue: `src/sqlery/core/claiming.py` (framework-agnostic, promoted from django_sqlery) duplicates `src/sqlery/django_sqlery/worker_claiming.py` (Django-specific, actively used). Both implement `get_node_id`, `check_tag_concurrency_limits`, `check_tag_rate_limits`, `check_job_dependencies`, `expire_ttl_jobs`, and `claim_next_job_with_queue_priority`. The Django version is the one actively called by `DjangoBackend.claim_job()` at `src/sqlery/django_sqlery/backend.py:150`. The core version is only imported by one file (`src/sqlery/worker_pool.py:14` for `get_node_id`).
- Files: `src/sqlery/core/claiming.py`, `src/sqlery/django_sqlery/worker_claiming.py`
- Impact: Bug fixes must be applied to both. The core version may drift out of sync since it is not the code path exercised by tests.
- Fix approach: Make `django_sqlery/worker_claiming.py` delegate to `core/claiming.py` (passing Django ORM as backend), or delete the core version and keep the Django-specific one until standalone mode is mature enough to need its own.

**Duplicate execution logic (core/worker.py vs django_sqlery/executor.py):**
- Issue: `src/sqlery/core/worker.py` (753 lines) and `src/sqlery/django_sqlery/executor.py` (675 lines) both implement job execution, retry logic, timeout handling, stale job cleanup, and concurrency checking. They use different patterns: the core version uses a backend abstraction; the Django version calls Django ORM directly. Both define `execute_job`, `_retry_job`, `_cleanup_stale_jobs`, `_kill_worker_process`, `can_execute_job`.
- Files: `src/sqlery/core/worker.py`, `src/sqlery/django_sqlery/executor.py`
- Impact: Maintenance burden doubles. Changes to retry logic, timeout behavior, or error handling must be replicated in both. Features like `parent_job_id` cascading and process group killing exist in core but not in the Django executor.
- Fix approach: Route Django mode through the core `JobExecutor` via `DjangoBackend`. The Django executor should become a thin wrapper or be deprecated.

**Pervasive commented-out code:**
- Issue: Large blocks of commented-out code throughout key files. `src/sqlery/core/worker.py` has ~130 lines of comments including old pipe IPC code, old kill-only-child code, old import logic. `src/sqlery/django_sqlery/executor.py` has ~71 comment lines. `src/sqlery/django_sqlery/models.py` has ~85 comment lines. `src/sqlery/django_sqlery/backend.py` has ~50 lines of commented-out old `claim_job` body.
- Files: `src/sqlery/core/worker.py`, `src/sqlery/django_sqlery/executor.py`, `src/sqlery/django_sqlery/models.py`, `src/sqlery/django_sqlery/backend.py`, `src/sqlery/compat/__init__.py`
- Impact: Obscures the actual implementation. Makes files appear larger than they are. The `# Old:` pattern appears as fossil comments throughout.
- Fix approach: Delete all commented-out code in a dedicated cleanup commit. The git history preserves old implementations.

**Django imports in the "framework-agnostic" core package:**
- Issue: `src/sqlery/core/` is documented as "Django-agnostic" but 8 of its 16 modules import directly from Django. For example, `core/worker.py` imports `django.db.close_old_connections` and `django.db.connections`. `core/db_resilience.py` imports `django.db.OperationalError`. `core/daemon.py` imports `django.conf.settings` and `django.utils.timezone`. `core/worker_pool.py` imports `django.conf.settings`.
- Files: `src/sqlery/core/worker.py:211,462,595`, `src/sqlery/core/db_resilience.py:50-51,74,102`, `src/sqlery/core/daemon.py:49,560,579`, `src/sqlery/core/worker_pool.py:61`, `src/sqlery/core/model_utils.py:55,120`, `src/sqlery/core/daemon_runner.py:21`, `src/sqlery/core/worker_runner.py:31`
- Impact: Standalone mode cannot use `core/worker.py` or `core/db_resilience.py` without Django installed. The abstraction boundary between core and django_sqlery is broken.
- Fix approach: Move all Django imports behind `try/except ImportError` blocks or route them through the compat layer. The `_reset_db_connections` method already has a try/except pattern that could be extended to `close_old_connections`.

## Known Bugs

**Missing webhooks module in django_sqlery:**
- Symptoms: Any job with a `webhook_url` set will raise `ImportError: No module named 'sqlery.django_sqlery.webhooks'` when `mark_success()` or `mark_failed()` is called.
- Files: `src/sqlery/django_sqlery/models.py:667`, `src/sqlery/django_sqlery/models.py:723`
- Trigger: Enqueue a job with `webhook_url` set, let it complete (success or failure).
- Workaround: The import is inside an `if self.webhook_url:` guard, so jobs without webhooks are unaffected. But for jobs with webhooks, the `mark_success`/`mark_failed` method will raise after already updating the DB status, which means the job status is updated but the webhook is never sent, and the error propagates to the worker.
- Fix: Change `from .webhooks import send_webhook_with_retry` to `from sqlery.webhooks import send_webhook_with_retry` (the top-level module exists at `src/sqlery/webhooks.py`).

**CI workflow triggers on `master` but default branch is `main`:**
- Symptoms: GitHub Actions CI never runs on pushes or pull requests targeting the default branch.
- Files: `.github/workflows/test.yml:6-8`
- Trigger: Push to `main` or open a PR targeting `main`.
- Workaround: None -- CI must be manually triggered or branch names changed.
- Fix: Change `branches: [ master ]` to `branches: [ main ]` on both push and pull_request triggers.

**`AsyncWorker` references removed backend abstraction:**
- Symptoms: `AsyncStorageBackend` is set to `None` at module level (`AsyncStorageBackend = None`). `AsyncWorker.__init__` accepts it as a type hint. Calling `await self.backend.claim_job(...)` will raise `AttributeError: 'NoneType' object has no attribute 'claim_job'` if no backend is explicitly provided.
- Files: `src/sqlery/async_worker.py:17`, `src/sqlery/async_worker.py:95`
- Trigger: Attempt to use `AsyncWorker` without passing an explicit backend object.
- Workaround: Always pass an explicit backend. But the class is effectively unusable in standalone mode since no async backend implementation exists.

## Security Considerations

**Arbitrary code execution via `task_path`:**
- Risk: The `task_path` field on `QueuedJob` is a string like `myapp.tasks.send_email`. It is imported and executed at `src/sqlery/core/utils.py:57-79` via `importlib.import_module()`. Any user or API client that can create jobs can execute any Python function accessible on the `PYTHONPATH`. This is by design for a task queue, but there is no allowlist or validation of task paths.
- Files: `src/sqlery/core/utils.py:57-79`, `src/sqlery/fastapi_sqlery/app.py:342-360`, `src/sqlery/django_sqlery/api_views.py`
- Current mitigation: Django API views require `is_staff` authentication. FastAPI standalone mode has NO authentication on any endpoint.
- Recommendations: Add a `ALLOWED_TASK_MODULES` config option that restricts which Python modules can be imported. At minimum, add authentication middleware to the FastAPI standalone dashboard. Document the security model clearly.

**FastAPI standalone dashboard has zero authentication:**
- Risk: The FastAPI app at `src/sqlery/fastapi_sqlery/app.py` exposes job creation, deletion, cancellation, database vacuum, and scheduled task management with no authentication. Any network-accessible deployment is fully open.
- Files: `src/sqlery/fastapi_sqlery/app.py:15-19` (no auth middleware), `src/sqlery/fastapi_sqlery/app.py:342` (`POST /api/jobs` creates jobs)
- Current mitigation: None. The Django mode uses `@staff_required_json` decorators properly.
- Recommendations: Add API key or basic auth middleware as a minimum. Add a config option `DASHBOARD_AUTH_ENABLED` (default True) that gates access.

**Webhook SSRF (Server-Side Request Forgery):**
- Risk: `src/sqlery/webhooks.py:123-128` sends HTTP POST requests to user-controlled `webhook_url` without any URL validation beyond Django's `URLField` format check. Internal network addresses (`http://169.254.169.254/`, `http://localhost:8080/admin`, `http://10.0.0.1/`) can be targeted.
- Files: `src/sqlery/webhooks.py:123-128`, `src/sqlery/django_sqlery/models.py:421-425`
- Current mitigation: Django `URLField` validates URL format only. No IP/host restrictions.
- Recommendations: Add an allowlist or denylist for webhook domains/IPs. At minimum, block RFC1918 private ranges and link-local addresses. Consider using a dedicated webhook delivery service.

**CSRF exemptions on internal endpoints:**
- Risk: `src/sqlery/django_sqlery/views.py:332` (`internal_worker`) and `src/sqlery/django_sqlery/api_views.py` endpoints use `@csrf_exempt`. The `internal_worker` view validates HMAC signatures, but all `api_views.py` admin endpoints rely solely on `@staff_required_json` (session cookie) without CSRF protection.
- Files: `src/sqlery/django_sqlery/api_views.py:209,281,375,453,475,607,807,840`
- Current mitigation: `@staff_required_json` checks `is_staff`. HMAC on internal worker endpoint.
- Recommendations: Either re-enable CSRF for admin API endpoints or use token-based auth (e.g., API keys) instead of session cookies. The JavaScript dashboard likely needs CSRF tokens in its fetch calls.

## Performance Bottlenecks

**Polling-based job claiming:**
- Problem: Workers poll the database for new jobs using `time.sleep(poll_interval)` (default 5 seconds). Under low-load conditions, this adds up to 5 seconds of latency to every job. Under high-load conditions, multiple workers hitting `SELECT FOR UPDATE SKIP LOCKED` creates contention.
- Files: `src/sqlery/core/worker.py:474-477,492-495,529` (multiple sleep loops), `src/sqlery/django_sqlery/worker_process.py:111-112`
- Cause: SQL databases do not natively support pub/sub notification for new row inserts. The architecture relies on periodic polling.
- Improvement path: On PostgreSQL, use `LISTEN/NOTIFY` to wake workers immediately when jobs are enqueued. On SQLite, reduce poll interval for low-latency queues. Consider an HTTP trigger mode (already partially implemented) as an alternative to polling.

**TTL expiry runs on every claim attempt:**
- Problem: `expire_ttl_jobs()` is called at the top of every `claim_next_job_with_queue_priority()` call at `src/sqlery/django_sqlery/worker_claiming.py:339`. This queries all queued jobs with TTL set and checks each one individually in Python, running inside the same transaction as the claim.
- Files: `src/sqlery/django_sqlery/worker_claiming.py:287-314,339`
- Cause: TTL expiry is coupled to the claiming hot path instead of being a periodic background task.
- Improvement path: Move TTL expiry to the daemon's periodic cleanup cycle (every N minutes) instead of running on every claim. Use a single `UPDATE ... WHERE created_at + ttl < now()` query instead of per-row Python iteration.

**Large model file (1196 lines):**
- Problem: `src/sqlery/django_sqlery/models.py` is 1196 lines containing 5 model classes, business logic methods, signal handlers, and display helpers. The `QueuedJob` model alone spans 580+ lines with methods like `mark_success`, `mark_failed`, `check_dependencies_met`, `then`, `force_stop`, and webhook delivery.
- Files: `src/sqlery/django_sqlery/models.py`
- Cause: Active Record pattern -- model instances carry business logic that should live in service layers.
- Improvement path: Extract business logic (mark_success, mark_failed, retry logic, webhook delivery) into a service module. Keep models as pure schema definitions.

## Fragile Areas

**Fork-based worker execution (core/worker.py):**
- Files: `src/sqlery/core/worker.py:548-680`
- Why fragile: The `_fork_and_execute` method uses `os.fork()`, `os.setpgrp()`, `os.killpg()`, `os.waitpid()`, and `os._exit()`. After fork, the child must close all inherited DB connections, reset signal handlers, and re-establish its own DB connection. Any failure in this sequence (e.g., DB connection pool corruption, signal handler inheritance) causes silent data loss or zombie processes. The parent waits in a polling loop (`time.sleep(0.5)`) checking `os.waitpid(WNOHANG)`.
- Safe modification: Never change the fork sequence without testing both the parent timeout path and the child crash path. Always test with PostgreSQL (connection sharing after fork is the most common failure mode). The `_reset_db_connections` call at line 563 (before fork) is critical.
- Test coverage: No dedicated fork tests exist. The chaos tests in `tests/chaos/test_worker_chaos.py` partially exercise this, but not the connection corruption scenario.

**Optimistic locking version field:**
- Files: `src/sqlery/django_sqlery/models.py:377-380`, `src/sqlery/django_sqlery/db_compat.py:63-108`
- Why fragile: The `version` field on `QueuedJob` is the sole mechanism for atomic claiming on SQLite. If any code path updates a job without incrementing the version field, it creates a silent race condition. The `ConcurrentModificationError` exception (raised when version conflicts occur) must be caught by all callers, but error handling is inconsistent -- `mark_success`, `mark_failed`, `mark_running` raise it, while `force_stop` at line 741 catches all exceptions silently.
- Safe modification: Always use `F('version') + 1` in UPDATE queries. Never use `job.save()` without `update_fields` that includes version-related changes. Test with concurrent workers on SQLite.
- Test coverage: `tests/test_version_locking.py` and `tests/test_atomic_claiming.py` cover the happy path.

**Global backend singleton initialization (compat/__init__.py):**
- Files: `src/sqlery/compat/__init__.py:691-709`
- Why fragile: `_backend` and `_config` are module-level globals initialized lazily without any locking. In a multi-threaded environment (e.g., Django under ASGI with threads), two threads could simultaneously enter `_initialize_backend()`, see `_backend is None`, and both create new backend instances. The last write wins, potentially losing in-flight state from the first.
- Safe modification: Add a threading lock around initialization, or initialize eagerly at app startup.
- Test coverage: No concurrent initialization tests exist.

**Daemon double-fork (core/daemon.py):**
- Files: `src/sqlery/core/daemon.py:242-270`
- Why fragile: Uses Unix double-fork to daemonize. Opens `/dev/null` for stdin/stdout/stderr redirection. PID file management at `/tmp/sqlery/sqlery_daemon.pid` has race conditions if multiple daemons start simultaneously. The heartbeat file mechanism at line 841 writes timestamps to a regular file, which can be corrupted by concurrent writes.
- Safe modification: Test daemon start/stop sequences thoroughly. The PID file should use advisory file locking (`fcntl.flock`) to prevent races.
- Test coverage: No daemon lifecycle tests exist.

## Scaling Limits

**SQLite concurrency ceiling:**
- Current capacity: SQLite with WAL mode and `busy_timeout=5000ms` (configured at `src/sqlery/django_sqlery/apps.py:23-27` and `src/sqlery/core/db_resilience.py:131-137`) supports approximately 1-3 concurrent workers before contention becomes significant.
- Limit: SQLite's write-lock serialization means only one writer at a time. Under high job throughput (>10 jobs/second), workers will frequently hit "database is locked" errors. The `retry_on_db_error` decorator at `src/sqlery/core/db_resilience.py:36` retries 3 times with exponential backoff, but this adds latency.
- Scaling path: Migrate to PostgreSQL for production workloads. The codebase already supports it via `SELECT FOR UPDATE SKIP LOCKED`.

**No connection pooling for standalone mode workers:**
- Current capacity: Each `SQLAlchemyBackend` instance creates sessions from a single global engine (`src/sqlery/fastapi_sqlery/database.py:21`). Worker processes spawned via fork inherit this engine, which can corrupt shared socket state.
- Limit: In standalone mode with forked workers, PostgreSQL connection pool state is shared across forks, leading to "connection already closed" or "SSL connection closed unexpectedly" errors.
- Scaling path: Use `NullPool` for forked workers or re-initialize the engine after fork (the Django side handles this via `_reset_db_connections`).

## Dependencies at Risk

**`requests` library used for webhooks without being a declared dependency:**
- Risk: `src/sqlery/webhooks.py:113-115` imports `requests` inside a try/except, logging an error if missing. But `requests` is not listed in any `pyproject.toml` dependency group. Users will silently get broken webhooks unless they independently install `requests`.
- Impact: Webhook delivery silently fails with an error log.
- Migration plan: Either add `requests` to an optional dependency group (e.g., `webhooks = ["requests>=2.25.0"]`) or switch to `httpx` which is already a declared dependency under `[project.optional-dependencies] http`.

**`AsyncStorageBackend` removed but `AsyncWorker` still references it:**
- Risk: `src/sqlery/async_worker.py:16-17` sets `AsyncStorageBackend = None` with a comment "REMOVED in v0.13". The `AsyncWorker` class still uses this type in its constructor. The entire async worker path appears non-functional.
- Impact: Anyone following documentation for async usage will hit runtime errors.
- Migration plan: Either remove `AsyncWorker` entirely or implement an async backend that wraps `DatabaseBackend`.

## Missing Critical Features

**No rate limiting on API endpoints:**
- Problem: The FastAPI and Django API endpoints have no rate limiting. A malicious or buggy client can flood the queue with jobs, exhaust database connections, or trigger thousands of webhook deliveries.
- Blocks: Production deployment without additional reverse proxy rate limiting.

**No job result size limits:**
- Problem: Job `output` and `error` fields are `TextField` (Django) / `Text` (SQLModel) with no size limits. A task returning a multi-megabyte string will be stored in full, bloating the database.
- Files: `src/sqlery/django_sqlery/models.py:566-568`, `src/sqlery/core/models.py`
- Blocks: Uncontrolled database growth from verbose task output.

## Test Coverage Gaps

**Standalone mode (FastAPI/SQLAlchemy backend) has no tests:**
- What's not tested: The entire `src/sqlery/fastapi_sqlery/` package (backend, app, database, config, CLI) has zero test files. The `src/sqlery/core/` package (worker, daemon, claiming, worker_pool, scheduler, registry) also lacks dedicated tests. All existing tests use Django test infrastructure (pytest-django).
- Files: `src/sqlery/fastapi_sqlery/backend.py` (888 lines), `src/sqlery/fastapi_sqlery/app.py` (581 lines), `src/sqlery/core/worker.py` (753 lines), `src/sqlery/core/daemon.py` (936 lines)
- Risk: The standalone mode could be entirely broken without anyone knowing. The core worker (which uses fork) is untested.
- Priority: High -- standalone mode is a primary use case.

**Django cleanup, views, and admin have no tests:**
- What's not tested: `src/sqlery/django_sqlery/cleanup.py` (337 lines, retention policies), `src/sqlery/django_sqlery/views.py` (902 lines, async views), `src/sqlery/django_sqlery/api_views.py` (860 lines, admin API), `src/sqlery/django_sqlery/admin.py` (623 lines), `src/sqlery/django_sqlery/backend.py` (897 lines, DjangoBackend implementation), `src/sqlery/django_sqlery/decorators.py` (492 lines), `src/sqlery/django_sqlery/worker_claiming.py` (523 lines)
- Files: All of the above
- Risk: Data retention bugs could silently delete jobs. API endpoints could return wrong data. The admin dashboard could crash.
- Priority: Medium -- these are operational/administrative features.

**Webhook delivery is untested:**
- What's not tested: `src/sqlery/webhooks.py` (257 lines) -- HMAC signing, retry logic, HTTP delivery, error handling.
- Files: `src/sqlery/webhooks.py`
- Risk: Webhook signatures could be computed incorrectly, breaking verification on the receiving end. The retry mechanism (noted as incomplete in the code at line 188: "Note: Actual retry scheduling would happen via a separate mechanism") is never exercised.
- Priority: Medium -- webhooks are an integration feature.

**No PostgreSQL-specific test coverage in CI:**
- What's not tested: CI runs only 2 test files against PostgreSQL (`test_atomic_claiming.py` and `test_atomic_scheduler.py`). The bulk of tests run against SQLite only. PostgreSQL-specific features like `SELECT FOR UPDATE SKIP LOCKED`, `VACUUM ANALYZE`, connection pool behavior after fork, and `statement_timeout` are not tested.
- Files: `.github/workflows/test.yml:61-66`
- Risk: PostgreSQL regressions go undetected. The `retry_on_db_error` decorator handles PostgreSQL-specific errors that are never triggered in SQLite tests.
- Priority: High -- PostgreSQL is the recommended production database.

---

*Concerns audit: 2026-05-12*
