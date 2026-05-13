# Codebase Concerns

**Analysis Date:** 2026-05-13

## Tech Debt

### Top-level `sqlery/*.py` shim files marked for cleanup

- Issue: 21 module files at `src/sqlery/` are header-stubs marked `# #CLEANUP: This file has been moved to src/sqlery/django_sqlery/` but remain on disk and on import paths.
- Files (representative): `src/sqlery/daemon_manager.py`, `src/sqlery/daemon_worker.py`, `src/sqlery/models.py`, `src/sqlery/subprocess_middleware.py`, `src/sqlery/apps.py`, `src/sqlery/admin.py`, `src/sqlery/cleanup.py`, `src/sqlery/worker_process.py`, `src/sqlery/dashboard_views.py`, `src/sqlery/worker_claiming.py`, `src/sqlery/executor.py`, `src/sqlery/settings.py`, `src/sqlery/db_compat.py`, `src/sqlery/subprocess_executor.py`, `src/sqlery/daemon_middleware.py`, `src/sqlery/http_trigger_middleware.py`, `src/sqlery/urls.py`, `src/sqlery/registries.py`, `src/sqlery/views.py`, `src/sqlery/middleware.py`, `src/sqlery/worker_registry.py`.
- Impact: Two import paths exist for the same symbol; doubles surface area for refactors, confuses IDE navigation, and prolongs deprecation cycle.
- Fix approach: Confirm no external/user code imports these paths, then delete in one batch (or downgrade to a `__getattr__`-based lazy re-export module per package).

### Dead async layer (`AsyncStorageBackend = None`)

- Issue: `AsyncStorageBackend` is set to `None` after the backends abstraction was removed in v0.13, but the `AsyncQueue` and async `Worker` classes still type-hint and reference it.
- Files: `src/sqlery/async_queue.py:11-55`, `src/sqlery/async_worker.py:18-264`, `src/sqlery/django_sqlery/queue.py:17`, `src/sqlery/schema.py:7-8`, `src/sqlery/django_sqlery/decorators.py:367,451`.
- Impact: Async worker / queue code paths can never be instantiated (defaults are `None`); ~600 lines of effectively-dead code that still gets imported.
- Fix approach: Either delete `async_queue.py` / `async_worker.py` entirely or rebuild them on top of `DatabaseBackend`.

### Legacy daemon manager wrapper

- Issue: `src/sqlery/django_sqlery/daemon_manager.py` is marked `DEPRECATED in v0.11.0: Use sqlery.core.daemon.DaemonManager instead.` and emits a deprecation warning on import.
- Files: `src/sqlery/django_sqlery/daemon_manager.py:3-25`.
- Impact: Carries deprecation noise across all Django mode users; risk if any legacy management command still imports it.
- Fix approach: Grep for callers, remove file, bump major version.

### `compat.rq` and `compat.scheduler` slated for removal in v3.2.0

- Issue: Both modules emit `DeprecationWarning` with explicit version targets but are still ~1500 LOC combined.
- Files: `src/sqlery/compat/rq.py:27`, `src/sqlery/compat/scheduler.py:34`.
- Impact: Backward-compat shims for RQ and django-tasks-scheduler. Need an explicit removal plan; otherwise they will silently outlive the target version.
- Fix approach: Set a removal commit gated on v3.2.0 changelog; add a CI check that fails if `version >= 3.2` and these modules still exist.

### Date-marked dead-code comments (per project convention)

The codebase intentionally preserves replaced code as `# # Old:` / `# Old:` comments per the user's [feedback_dead_code] memory. These should be tracked so they can be aged out:

- `src/sqlery/worker_pool.py:40` and `src/sqlery/core/worker_pool.py:106` — old "always redirect to raw log file" logic.
- `src/sqlery/core/worker.py:443,704,722` — old kill-only-child fork logic (now kills process group).
- `src/sqlery/core/daemon.py:224,505,511,572,833` — old logging/lock/heartbeat/zombie-detection behavior, including a global `os.kill` zombie scan that was unsafe on multi-node setups.
- `src/sqlery/core/worker_runner.py:15` — old "log to stderr" path.
- `src/sqlery/core/scheduler.py:103` — old "always used cron" code that broke `interval` and `once` schedule types.
- `src/sqlery/django_sqlery/daemon_worker.py:235` — old stdout-logging path.
- `src/sqlery/django_sqlery/models.py:924` — old in-Python filtering that loaded all queued jobs into memory.
- `src/sqlery/django_sqlery/admin.py:326`, `src/sqlery/django_sqlery/api_views.py:317`, `src/sqlery/django_sqlery/views.py:67` — old fork-unaware worker-kill / aggregation queries.
- `src/sqlery/django_sqlery/migrations/0023_restore_daemonlease.py:37` — old unconditional `CreateModel` that crashed on existing tables.

None of these blocks carry an explicit removal date; recommend adding a `# Remove after YYYY-MM-DD` comment so the tracking convention has a deadline.

### `TODO` markers

- `src/sqlery/core/log_config.py:8`: "TODO: Decide on the best default logging strategy for non-debug mode." — Only TODO in the codebase. Log configuration policy is unfinished.

## Known Bugs / Limitations

### SQLite cannot use `SELECT FOR UPDATE SKIP LOCKED`

- Symptoms: Under multi-worker SQLite, claiming falls back to optimistic locking (`QueuedJob.version` CAS), which has higher contention and retry storms.
- Files: `src/sqlery/django_sqlery/worker_claiming.py:229`, `src/sqlery/django_sqlery/db_compat.py:46`, `src/sqlery/compat/__init__.py:68,223,253`, `src/sqlery/django_sqlery/models.py:387,620-646`.
- Trigger: 2+ workers on SQLite contending for the same queue.
- Workaround: WAL mode + `busy_timeout=5000` are auto-applied (`src/sqlery/core/db_resilience.py:140-147`, `src/sqlery/django_sqlery/apps.py:23`). Documentation should explicitly recommend Postgres for multi-worker production.

### Fork-safety pitfalls

- Symptoms: DB connections inherited across `os.fork()` corrupt the parent's session if the child closes them; manifests as `psycopg` `OperationalError: connection already closed` or "consuming input failed".
- Files: `src/sqlery/core/worker.py:563` (`_fork_and_execute`), `src/sqlery/core/worker.py:235` (commented-out task-loader fork path), `src/sqlery/core/worker_pool.py:118-131` (FD leak comment).
- Trigger: Any unhandled exception during the fork window or any DB call inside a SIGUSR1 signal handler.
- Workaround: `_reset_db_connections()` is called pre/post fork; signal handlers only flip a flag — never call DB. This contract is not asserted in code; one careless future edit will reintroduce the bug.

### FD leak risk in worker pool

- Symptoms: One file descriptor leaked per worker spawn under certain code paths.
- Files: `src/sqlery/core/worker_pool.py:131` notes "Leaving it open leaks one FD per spawn."
- Impact: Long-running daemons (weeks-long uptime) can exhaust file descriptors.

### Circular import fragility

- Files: `src/sqlery/__init__.py` (conditional Django decorator import), `src/sqlery/compat/__init__.py` (forced absolute imports).
- Fragility: Comments note the compat layer uses absolute imports (`from sqlery.django_sqlery.backend`) instead of relative to avoid resolution in the wrong package. Any future contributor switching to relative imports will break standalone mode.
- Fix approach: Add an import-time integration test that imports `sqlery` from both a Django and a non-Django environment.

## Security Considerations

### Webhook delivery — SSRF risk

- Risk: `send_webhook()` posts to a user-controlled `job.webhook_url` with no host allowlist, no scheme restriction, no private-IP filtering.
- Files: `src/sqlery/webhooks.py:135-157`.
- Current mitigation: HMAC-SHA256 signature header (`X-Sqlery-Signature`) if `WEBHOOK_SECRET` is set; 10s default timeout.
- Recommendations:
  - Validate `webhook_url` scheme is `https://` (or explicit allowlist).
  - Block RFC 1918 / link-local / loopback hosts unless explicitly opted-in.
  - Require `WEBHOOK_SECRET` in production (currently optional; if `None`, signature is silently skipped at `webhooks.py:108-110`).

### `requests` dependency missing from `pyproject.toml`

- Risk: `src/sqlery/webhooks.py:17` does `import requests` (with try/except → `requests = None`), but `requests` is not listed in any `[project.optional-dependencies]` group. The error message at `webhooks.py:127` advises `pip install sqlery[webhooks]` — that extra does not exist in `pyproject.toml`.
- Impact: Webhook delivery silently degrades to a logged error in every install path; users following the docs cannot resolve it.
- Fix approach: Add `webhooks = ["requests>=2.31.0"]` (or migrate to the already-required `httpx`) and update the install hint.

### Untrusted task path → arbitrary import

- Risk: `getattr(module, function_name)` after `importlib.import_module(module_name)` runs whatever the enqueuer wrote into `task_path`.
- Files: `src/sqlery/utils.py:63`, `src/sqlery/core/utils.py:72`, `src/sqlery/async_worker.py:160-166`.
- Current mitigation: Enqueue API is in-process Python; assumption is the enqueuer is trusted. No allowlist of importable modules.
- Recommendations: Document the trust boundary; consider an opt-in `ALLOWED_TASK_MODULES` setting for environments where the queue may receive untrusted writes (multi-tenant DB).

### `kwargs` deserialization

- Risk: Job kwargs are JSON-decoded (not pickled — good) but go straight into the function call. Functions that themselves `pickle.loads` user data are vulnerable.
- Files: throughout `src/sqlery/core/worker.py`, `src/sqlery/django_sqlery/executor.py`.
- Current mitigation: JSON-only serialization avoids the classic pickle RCE.

### Lambda IAM — undocumented privilege requirements

- Risk: `src/sqlery/lambda_handler.py` calls `invoke_lambda_worker()` to chain Lambda → Lambda invocations; `src/sqlery/eventbridge_trigger.py` creates EventBridge rules. Required IAM permissions (`lambda:InvokeFunction`, `events:PutRule`, `events:PutTargets`, `iam:PassRole`) are not documented in-tree.
- Files: `src/sqlery/lambda_handler.py:65,159,193,273,326`, `src/sqlery/eventbridge_trigger.py:50-150`.
- Recommendations: Add a minimal IAM policy template under `docs/lambda/`. Currently a deployer needs to read source to discover them.

### Raw SQL in cleanup/VACUUM paths

- Files: `src/sqlery/django_sqlery/cleanup.py:179,342`, `src/sqlery/django_sqlery/backend.py:487-497`, `src/sqlery/fastapi_sqlery/backend.py:422-425`, `src/sqlery/core/db_resilience.py:140-165`.
- Risk: SQL strings are hardcoded table/PRAGMA names (no string interpolation of user input) — low risk. `f"SET statement_timeout = '{int(statement_timeout_ms)}'"` is wrapped in `int()`, but the f-string pattern is fragile.
- Recommendation: Switch to parameterized values where supported; flag the f-string pattern in code review guidelines.

### `subprocess.Popen` with `env=os.environ`

- Files: `src/sqlery/django_sqlery/subprocess_middleware.py:94`, `src/sqlery/django_sqlery/executor.py:674`, `src/sqlery/core/daemon.py:234`, `src/sqlery/core/worker_pool.py:118`.
- Risk: Inherits the full parent environment (intentional — needed for `DJANGO_SETTINGS_MODULE`), but means any env leak (logs, error reports) will surface secrets. `shell=True` is not used anywhere (good).

## Performance Bottlenecks

### Scheduler / zombie scan queries without LIMIT

- Files: `src/sqlery/core/scheduler.py`, `src/sqlery/core/daemon.py:526` (`_fail_zombie_running_jobs`).
- Concern: Zombie scan iterates all running jobs on each daemon cycle; on large queues this is O(running_jobs). No incremental cursor.

### VACUUM on every cleanup run

- Files: `src/sqlery/django_sqlery/cleanup.py:342-344`, `src/sqlery/fastapi_sqlery/backend.py:422-425`.
- Concern: `VACUUM ANALYZE` blocks autovacuum on Postgres; on SQLite, full `VACUUM` rewrites the database file. Should be opt-in or scheduled, not part of each cleanup cycle.

### Optimistic locking retry storms (SQLite)

- Files: `src/sqlery/django_sqlery/models.py:620-650`.
- Concern: Under contention, `version=expected_version` CAS causes retry loops with no backoff. Workers can busy-loop hammering SQLite.

## Reliability Concerns

### Zombie / orphan job detection has a 5-signal heuristic

- Files: `src/sqlery/core/daemon.py:526-572`.
- Concern: Detection requires 5 conditions (PID gone, no worker, worker dead, worker moved on, heartbeat stale ≥ 3× `WORKER_ALIVE_TIMEOUT`). False negatives leave jobs `running` indefinitely.
- Risk: A job that succeeded but failed to write its terminal state (DB drop at the wrong moment) appears identical to a zombie.

### Heartbeat gaps under SIGUSR1 pressure

- Files: `src/sqlery/core/daemon.py:505` ("Remove file-based heartbeat (deprecated but cleanup anyway)").
- Concern: Heartbeats moved from file-based to DB-based via SIGUSR1. If the worker is mid-fork or in a long-running C extension, SIGUSR1 may be delayed > the 3×alive_timeout window, marking a live worker as zombie.
- Mitigation: 3× safety factor; document tuning of `WORKER_ALIVE_TIMEOUT`.

### Signal handler discipline is unenforced

- Files: `src/sqlery/core/worker.py`, `src/sqlery/core/daemon.py`.
- Concern: ARCHITECTURE notes "no DB calls in signal handlers to avoid corrupting psycopg connections" — this is a comment, not an enforced contract.
- Recommendation: Add a lint rule or test that scans signal handlers for DB imports.

### Two-layer timeout race

- Files: `src/sqlery/core/worker.py:443,704,722`.
- Concern: Child SIGALRM at `timeout`, parent safety SIGKILL at `timeout + 60s`. If parent clock skew > 60s vs child (containers with frozen clocks), parent may kill prematurely or never.

### `version=0.13.0` in `pyproject.toml` vs deprecation targets at `v3.2.0`

- Files: `pyproject.toml` (version 0.13.0), `src/sqlery/compat/rq.py:27`, `src/sqlery/compat/scheduler.py:34`.
- Concern: Deprecation messages reference `v3.2.0` but project is at `0.13.0`. Mismatch indicates either an upcoming major version bump or stale deprecation messages. Users cannot tell which.

## Fragile Areas

### `src/sqlery/django_sqlery/models.py` (1248 lines)

- Why fragile: Largest file in the project; combines model definitions, custom managers, optimistic locking, version increment logic, and historical migration helpers (`# Old:` blocks).
- Safe modification: Add new fields via Django migrations only; never edit existing field defaults without a data migration. The `version` field is load-bearing for SQLite claiming.
- Test coverage: `tests/test_models.py`, `tests/test_version_locking.py` — adequate but not exhaustive on edge cases.

### `src/sqlery/compat/__init__.py` (900 lines)

- Why fragile: Defines the ABC contract for both backends. Adding a method requires implementing it in `DjangoBackend` AND `SQLAlchemyBackend`, or both backends fall out of sync silently (ABC abstract methods are enforced, but new helpers are easy to add only in one place).
- Safe modification: Always touch all three files together: `src/sqlery/compat/__init__.py`, `src/sqlery/django_sqlery/backend.py`, `src/sqlery/fastapi_sqlery/backend.py`.

### `src/sqlery/core/worker.py` (769 lines) — fork orchestration

- Why fragile: Fork-per-job semantics, signal forwarding, two-layer timeout, FD lifecycle. Three `# # Old: killed only the child` markers show this has been wrong before.
- Test coverage: `tests/chaos/test_worker_chaos.py`, `tests/test_concurrency_and_timeout.py`.

### `src/sqlery/core/daemon.py` (973 lines)

- Why fragile: Owns lease acquisition, heartbeat, scheduler tick, zombie scan. Five `# # Old:` blocks document past bugs (global os.kill, file locks, daemon races writing status).

## Scaling Limits

### SQLite single-writer

- Current capacity: ~100 jobs/sec on commodity SSD with WAL mode.
- Limit: One concurrent writer; `database is locked` errors above this.
- Scaling path: Switch to Postgres (documented as production recommendation).

### Worker pool size capped by `DJANGO_SQL_JOBS_MAX_WORKERS`

- Files: `src/sqlery/core/worker_pool.py`.
- Limit: No per-host CPU/memory checks; oversubscribing is possible.

## Dependencies at Risk

### `requests` not declared

- Risk: Imported in `src/sqlery/webhooks.py:17` but absent from `pyproject.toml`. CLAUDE.md notes this explicitly.
- Impact: Webhooks silently disabled on clean install.
- Migration plan: Add to a new `webhooks` extra, or replace with `httpx` (already a dependency via the `http` extra).

### `boto3` only in `eventbridge` extra

- Files: `src/sqlery/eventbridge_trigger.py`, `src/sqlery/lambda_handler.py`.
- Risk: Importing `lambda_handler` without the extra → `ImportError`. Lambda deployment instructions must mention the extra.

### `django-tasks` is optional but referenced unconditionally

- Files: `src/sqlery/django_sqlery/` integrations.
- Risk: Need to verify all `django_tasks` imports are wrapped in try/except (per CONVENTIONS); worth a grep audit.

## Missing Critical Features

### No CSRF/auth on FastAPI dashboard

- Files: `src/sqlery/fastapi_sqlery/app.py`.
- Problem: REST API for standalone mode has no documented auth layer.
- Blocks: Production deployment behind a reverse proxy is the only safe option.

### No rate limit on webhook retries

- Files: `src/sqlery/webhooks.py:170+` (`send_webhook_with_retry`).
- Problem: Retries could pile up on a slow/down receiver.

## Test Coverage Gaps

### Webhook delivery — no tests

- What's not tested: `src/sqlery/webhooks.py` (HMAC signing, retry logic, error handling, SSRF surface).
- Risk: Webhooks silently broken since `requests` not in deps; no test would catch it.
- Priority: High.

### Lambda handler / EventBridge — no tests

- What's not tested: `src/sqlery/lambda_handler.py`, `src/sqlery/eventbridge_trigger.py`.
- Risk: Serverless mode could regress on any refactor.
- Priority: High (medium if Lambda mode is non-critical).

### Async worker / queue — no tests, no users

- What's not tested: `src/sqlery/async_queue.py`, `src/sqlery/async_worker.py`.
- Risk: Code is dead (`AsyncStorageBackend = None`); tests would fail. Decide: delete or rebuild.
- Priority: Medium (cleanup task).

### Standalone (FastAPI/SQLAlchemy) backend

- What's not tested: `src/sqlery/fastapi_sqlery/backend.py` (891 lines). Most existing tests target Django mode.
- Risk: One of the two declared "integration modes" is undertested.
- Priority: High — this is the project's headline value proposition ("Every execution mode works reliably and is tested in CI across both Django and standalone").

### `tests/integration/` is empty

- Files: only `__init__.py` exists.
- Risk: No end-to-end test of the standalone CLI → daemon → worker → DB flow.
- Priority: High.

### Lambda interactions exercised only indirectly

- Files: `tests/chaos/test_worker_chaos.py`, `tests/chaos/test_property_based.py`.
- Risk: Lambda + Postgres + fork interactions are not exercised.

---

*Concerns audit: 2026-05-13*
