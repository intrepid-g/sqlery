# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

## [0.27.0] - 2026-10-26

### Upgrade notes

- **Stop all workers, migrate, then start workers.** New migrations: Django `0042_claim_without_update`, Alembic `20261026_0023`. A worker started before the migration still claims with the old row UPDATE and would not be seen as running.
- **BREAKING for direct table access: a running job's live row now reads `status = 'queued'`.** Running means "has an open `sqlery_job_attempt`". `QueuedJob.objects.filter(status='running')` and raw SQL for `running` on `sqlery_queued_job` no longer match; use `JobAll` / `sqlery_job_all`, which still report `running` with `started_at`, `worker_id`, `worker_pid` and `child_pid` taken from the attempt. The migration converts rows stored as `running`.
- **Live-table partitions no longer follow `SQLERY_PARTITION_RETENTION`.** Drained live partitions are dropped within about two hours (#156). New settings: `SQLERY_LIVE_PARTITION_INTERVAL` (default `1 hour`, new partitions only) and `SQLERY_LIVE_PARTITION_GRACE` (default `1 hour`). Standalone partition maintenance now runs every 5 minutes.

### Changed

- **A claim no longer updates the job row (#157, #159).** It deletes the ready entry and inserts an attempt; the job row is written only at enqueue and at completion. This removes the last per-job dead tuple from the claim path. A partial index of open attempts by job backs the running-state lookups.
- **Drained live-job partitions are dropped within about two hours, not after 30 days (#156).** On partitioned PostgreSQL, a `sqlery_queued_job` partition is dropped when its upper bound is older than `SQLERY_LIVE_PARTITION_GRACE` (new, default `1 hour`), it is not the DEFAULT partition and it holds no row at all (rows of any status pin it). The emptiness check and the drop run under `LOCK TABLE ... ACCESS EXCLUSIVE NOWAIT`, with a 50 ms `lock_timeout` for the parent lock `DETACH` needs, so claims and enqueues never queue behind it; a failure retries on the next tick. `SQLERY_PARTITION_RETENTION` no longer applies to the live table (history and attempts keep it). New partitions of the live table are one hour wide (`SQLERY_LIVE_PARTITION_INTERVAL`, new, default `1 hour`; set `1 day` for the old shape) with at least a day premade; existing partitions are not repartitioned and mixed widths never overlap or leave gaps. The standalone default of `PARTITION_MAINTENANCE_INTERVAL_MINUTES` is now 5 (was 1440).
- **Health reports drained live partitions that cannot be dropped (#156).** `xmin_horizon.live_partitions` gives their number and age and the number still holding rows, and health is `degraded` past `PG_LIVE_PARTITION_STUCK_DEGRADED_SECONDS` (default 900, 0 disables). See `docs/TROUBLESHOOTING.md` and `docs/design/queue-robustness.md`.

## [0.26.1] - 2026-10-26

### Fixed

- **Every claim is fenced on status and version (#30, #153).** The legacy Django helpers `atomic_claim_job_postgres` / `_sqlite` (re-exported as `sqlery.db_compat.atomic_claim_job`) now delete the ready entry first (rowcount 1 is the claim) and update with `status = 'queued' AND version = <read>`, rolling back on a lost race. Before this, a candidate cancelled by raw SQL could still be claimed. The other claim paths gain the same `version` predicate.
- **TTL expiry persists in standalone mode (#29, #154).** `expire_ttl_jobs` changed detached rows, so a queued job past its TTL stayed claimable (for example through `claim_job_by_id`). It now expires through the backend's fenced path: the job moves to history and its ready and blocked entries are removed. A naive `created_at` counts as UTC.

## [0.26.0] - 2026-10-26

### Upgrade notes

Read these before upgrading; this release changes the schema and where finished jobs live.

- **Stop all workers, migrate, then start workers.** New tables are required, and workers refuse to start without them (`sqlery_job_attempt`, `sqlery_job_ready`, `sqlery_job_blocked`, `sqlery_job_history`). Django: `0036_drop_redundant_job_indexes`, `0037_job_attempt`, `0038_job_ready_queue`, `0039_job_history`, `0040_move_terminal_jobs`, `0041_ready_rotation`. Alembic (standalone): `20261026_0019` (attempts), `20261026_0020` (ready queue), `20261026_0021` (history), `20261026_0022` (rotation sequences). Standalone start-up without Alembic creates the same objects.
- **BREAKING: finished jobs leave `sqlery_queued_job`.** Terminal jobs (`success`, `failed`, `archived`) are moved to the insert-only `sqlery_job_history`. `QueuedJob.objects` and raw SQL on `sqlery_queued_job` now see only queued, running and transiently-finished jobs (a failed job owing a retry, a finished job whose webhook is still being delivered). To read jobs in any state use the read-only `JobAll` model (Django and SQLModel) or the `sqlery_job_all` view. Retry changed with it: `retry_failed_jobs` can no longer re-queue a history row, it creates a fresh child job with `parent_job_id` set to the failed job (once per failure). `cleanup_jobs` now only touches history, aged from `finished_at`.
- **`worker_pid` / `child_pid` now live on `sqlery_job_attempt`**, one row per claim, not on the job row. Code reading the PIDs from the job must read the open attempt instead.
- **A raw `UPDATE sqlery_queued_job SET status = 'queued'` is no longer enough to make a job claimable.** It becomes claimable only when the ready-queue sweep restores it (within `READY_QUEUE_SWEEP_INTERVAL`, default 60 s). Go through the library (`retry_failed_jobs`, `QueuedJob.objects.update(status="queued")`, `release_job`) for immediate effect. Terminal rows left in the live table by raw SQL are moved to history by the same sweep after `HISTORY_SETTLE_GRACE_SECONDS` (60).
- **Standalone mode now applies PostgreSQL session timeouts to every pooled connection.** Previously a bug skipped them (the SQLAlchemy backend has no `vendor`, so resilience setup returned early): `statement_timeout` 30 s, `lock_timeout` 10 s and the new `idle_in_transaction_session_timeout` 60 s are now set on each new pooled connection. Long-running statements or sessions idling inside a transaction beyond these limits are now cancelled by PostgreSQL. Tune with `PG_STATEMENT_TIMEOUT_MS`, `PG_LOCK_TIMEOUT_MS`, `PG_IDLE_IN_TRANSACTION_TIMEOUT_MS` (env `SQLERY_<NAME>`; `0` disables). Job child processes still skip `statement_timeout` and the idle timeout. Django mode already applied the first two.
- **Migration time.** `0040_move_terminal_jobs` (and Alembic `20261026_0021`) copy every existing terminal row into history in batches of 1000, one transaction each; the time scales with the number of terminal rows. It is not atomic and is resumable: if interrupted, run it again. Consider running `cleanup_jobs` first to shrink the table. The reverse moves the rows back.
- **New opt-in:** ready-table rotation (`READY_ROTATION_ENABLED`, PostgreSQL only) is off by default; nothing changes until you enable it.

### Changed

- **Claiming reads a ready queue (#139, #146).** Workers claim from `sqlery_job_ready` (one narrow entry per claimable job; `PARTITION BY LIST (slot)` on PostgreSQL) instead of scanning `sqlery_queued_job`, and jobs waiting on dependencies sit in `sqlery_job_blocked` until a dependency finishes instead of being re-examined by every claim. Enqueue, retry, requeue, cancel, expiry and the other paths that make a job claimable or not write their entry in the same transaction as the job row; a periodic sweep run by the scheduler leader (`READY_QUEUE_SWEEP_INTERVAL`, default 60 s, `READY_QUEUE_SWEEP_TIMEOUT`, default 10 s) repairs entries lost to raw SQL. The claiming-only partial index `sqlery_job_pending_idx` is dropped.
- **Finished jobs move to insert-only history (#140, #148).** Success, final failure and archival are an `INSERT .. SELECT` into `sqlery_job_history` plus a `DELETE` from the live table, fenced by the row's `version`; a stale completion raises `JobFencingError` and writes nothing. `sqlery_job_all` (`JobAll`) is a read-only union of live and history; `get_job_by_id`, retry chains (`parent_job_id`), dependency judgement, `AsyncResult` and the dashboard read across both. A job with a `webhook_url` stays in the live table until delivery resolves. `cleanup_jobs` drops history only; on partitioned PostgreSQL the daemon creates and drops daily partitions of the live table, history and attempt table. The `started_at DESC` / `finished_at DESC` indexes of the live table are dropped (Django). The scheduler leader's sweep also settles terminal rows stuck in the live table. Data migration: Django 0039 (schema) and 0040 (data), Alembic `20261026_0021`.
- **Redundant indexes dropped (#136, #143).** `(task_path, status)` and `(created_at DESC)` on `sqlery_queued_job` are removed (Django migration 0036): neither served a query, and the first defeated HOT updates because `status` changes on every transition.
- **Jobs record fenced attempts (#138, #145).** Every claim inserts a `sqlery_job_attempt` row with a fresh token in the claim transaction; completions close exactly their own attempt in the same transaction as the job update and are rejected with `JobFencingError` if it is no longer open; recovery abandons the open attempt. Kill, zombie detection and the dashboard read the child PID from the attempt. Django `0037_job_attempt`, Alembic `20261026_0019`.
- **Health reports the xmin horizon and table bloat (#137, #144).** New `idle_in_transaction_session_timeout` (`PG_IDLE_IN_TRANSACTION_TIMEOUT_MS`, default 60000) on sqlery's own sessions, so an idle-in-transaction session cannot pin the xmin horizon and stop VACUUM. `/health` and the dashboard gain an `xmin_horizon` block and go `degraded` past `PG_XMIN_AGE_DEGRADED_SECONDS` (300), `PG_DEAD_TUPLE_RATIO_DEGRADED` (0.5, with at least `PG_DEAD_TUPLE_MIN` = 10000 dead tuples on `sqlery_queued_job`) or `PG_SLOT_XID_AGE_DEGRADED` (5000000 xids for a replication slot or standby). See `docs/TROUBLESHOOTING.md`.

### Added

- **Opt-in ready-table rotation (#141, #149).** With `READY_ROTATION_ENABLED` (default off, PostgreSQL only) the scheduler leader rotates `sqlery_job_ready` through three slots and reclaims drained ones with `TRUNCATE` instead of waiting for VACUUM, bounding its storage by the interval times the write rate. Tunables: `READY_ROTATION_INTERVAL` (60 s), `READY_ROTATION_DRAIN_BATCH` (1000), `READY_ROTATION_LAG_DEGRADED_SECONDS` (0 = 3 x interval + 60). Health gains a `ready_rotation` block with the lag and goes `degraded` past the threshold. Django `0041_ready_rotation` and Alembic `20261026_0022` create three sequences (reversible). If a slot cannot be reclaimed the ring stops switching and behaviour equals the non-rotating mode. Design: `docs/design/queue-robustness.md`.
- `run_jobs --max-jobs N` and `--idle-linger S`, plus the `SUBPROCESS_JOBS_PER_PROCESS` (default 1) and `SUBPROCESS_IDLE_LINGER_S` (default 0) settings passed through by the subprocess trigger. Defaults keep one job per process.

### Fixed

- **Claiming no longer starves behind blocked candidates, and tag locks are try-locks (#135, #142).** The claim loop pages through candidates until exhausted, excluding every inspected id, so jobs with unmet dependencies or tag limits at the head of the queue cannot hide runnable work. `acquire_tag_locks` no longer waits: a candidate whose tag lock is held by another worker is treated as tag-limited and skipped.
- **Staging promotion (#147, #150).** Promoting a due staged job into `sqlery_queued_job` now builds its INSERT from the table's real columns (read from the catalog) instead of a hard-coded list, so it works against both the standalone and the Django schema. A column the staged row lacks gets the default the normal enqueue path would give (Django field defaults, SQLModel/SQLAlchemy defaults), then its database default, then a neutral value with a warning. Webhook settings staged with the job (`webhook_url`, `webhook_events`, `webhook_max_retries`) are carried through promotion instead of being dropped. Promotion also writes its ready entry in the slot current writers use when rotation is enabled.
- `SubprocessTriggerMiddleware` no longer spawns `run_jobs` without bound (#113). New `MAX_SUBPROCESS_WORKERS` setting (default 2, `0` = unbounded, the old behaviour) caps the `run_jobs` processes alive at once, counting both middleware spawns and the `run_jobs` successor chain (a chain hands its slot to its successor, so it occupies one). Slots are `flock`ed files in `SUBPROCESS_SLOT_DIR` (default: a per-project directory under the system temp dir), released by the kernel even if a child is SIGKILLed; not enforced where `fcntl` is unavailable (Windows). When all slots are busy the running chains keep draining the queue.
- `AsyncWorker` now keeps up to `concurrency` jobs in flight (default 10, new constructor argument) as documented, instead of running one at a time, so I/O-bound coroutine jobs overlap on a single worker. Its jobs also go through `invoke_task` semantics (new `ainvoke_task`): chain/chord steps built with `.s()` receive the upstream result, `bind=True` tasks get a `TaskContext`, and `task_prerun`/`task_postrun`/`task_success`/`task_failure` fire. Signal receivers run on the event loop, so ORM work in them must be async-safe.
- Standalone mode applied none of the PostgreSQL session timeouts (see Upgrade notes); it now applies `statement_timeout`, `lock_timeout` and `idle_in_transaction_session_timeout` to every pooled connection.

## [0.25.0] - 2026-10-15

### Upgrade notes

Read these before upgrading; each is a deliberate behaviour change.

- **`croniter` is no longer a dependency.** It was declared but never imported: cron parsing and next-run calculation have always been done by Sqlery's own `sqlery.crontab`. If your own code imports `croniter`, install it yourself.
- **Webhooks use `httpx`, not `requests`.** `requests` was imported but never declared (and the error message pointed at a `sqlery[webhooks]` extra that did not exist). Webhooks now use `httpx`, which is already a declared dependency; install with `pip install sqlery[webhooks]`. Code or tests that patched `sqlery.webhooks.requests` must patch `sqlery.webhooks._post_pinned` (or `httpx`) instead.
- **Tag limits are now enforced.** `TAG_CONCURRENCY_LIMITS` and `TAG_RATE_LIMITS` were silently ignored in both modes. A deployment that already has them configured will start being throttled. Set tags with `@job(tags=[...])`.
- **Dashboard auth behind a proxy.** The FastAPI dashboard used to skip its API-key auth whenever `scope["root_path"]` was non-empty, so `uvicorn --root-path /x` left the admin API open. Only a real Starlette `Mount` (or `SQLERY_DASHBOARD_AUTH=inherit`) disables it now. If you relied on a bare `root_path` to skip auth, set `SQLERY_DASHBOARD_AUTH=inherit` or `disabled` explicitly.
- **Result output format.** Dict/list results are stored in `output` as JSON instead of a Python repr; strings are unchanged and falsy results (`0`, `False`, `[]`) are now kept.
- **Still opt-in (recommended):** `ALLOWED_TASK_MODULES` defaults to allow-all (anyone able to write job rows can run any importable function); set it. `REQUIRE_BODY_SIGNATURE=True` rejects legacy timestamp-only trigger signatures; enable it once your signers send `X-Signature-Version: 2`.

### Security

- Webhook delivery is now DNS-rebinding safe: the URL is resolved and validated once, and the request connects to *that* IP (original `Host` header and TLS SNI/certificate hostname preserved, redirects off). Previously the HTTP client re-resolved the name after validation.
- Task arguments are no longer written to the logs by default (only the number of positional args and the keyword *names*); set `LOG_TASK_ARGUMENTS=True` to log values while debugging.
- Optional Lambda event authentication: set `LAMBDA_SHARED_SECRET` (or `SQLERY_LAMBDA_SECRET`) on the worker and invoke with `sqlery.core.lambda_core.sign_event(event, secret)`; unsigned, tampered or stale (`LAMBDA_SIGNATURE_MAX_AGE`, default 300s) events get a 403. Off by default, so IAM-only setups are unchanged.
- Webhook delivery no longer follows HTTP redirects, closing an SSRF bypass where a validated public URL redirected to an internal address.
- HTTP trigger signatures can now bind the request body (`X-Signature-Version: 2`, `make_signed_request_headers(secret, body=...)`), so a captured header pair cannot be replayed with a different payload. Legacy timestamp-only signatures keep working; set `REQUIRE_BODY_SIGNATURE` (or `SQLERY_REQUIRE_BODY_SIGNATURE=1`) to reject them.
- Lambda handler no longer logs the full invocation event (only action and keys).

### Fixed

- Django mode: `claim_job` returned nothing for any claimer whose id was not `worker_<node>_<pid>` or a registered UUID, so the Lambda handler's and the HTTP trigger's queue claims (`lambda-<uuid>`, `trigger-<uuid>`) silently processed no jobs. The Lambda and HTTP-trigger claimers now claim normally; any other unregistered worker id is still refused.
- A worker killed between failing a job and creating its retry silently lost the retry: the job stayed `failed` with retries left, indistinguishable from an ordinary failure, and no sweeper looked for it. A job that will be retried is now failed with a `retry_pending` marker (stored in the existing `termination_reason`, original reason preserved as `retry_pending:<reason>`), and `recover_lost_retries()` creates the retry for any such job older than a grace period. Workers run it every `RETRY_RECOVERY_INTERVAL` seconds (default 30, `0` disables; Django's stale-job sweep calls it too). `_retry_job` is now idempotent, so a recovery sweep racing the original worker, or the stale-job sweeper, never creates a duplicate; the marker is cleared (original reason restored) once the retry exists. Failures that must not retry (`autoretry_for` mismatch, retries exhausted, revoked) are never marked, so they are never resurrected. `mark_job_failed` gains an optional `termination_reason`; backends that lack it keep working without the marker. The new `DatabaseBackend` hooks (`get_jobs_pending_retry`, `settle_retry_pending`) and `get_latest_retry` default to harmless no-ops.

- `on_success` / `on_failure` job callbacks never ran under the standalone `JobExecutor`, which also serves `WorkerProcess` (daemon and `sqlery-worker`), the HTTP trigger and Lambda; only the Django `TaskExecutor` invoked them. Both executors now share `run_success_callback` / `run_failure_callback` (`sqlery.core.execution`) with identical semantics: `cb(job, None, result)` after success and `cb(job, None, None)` after each failed attempt, before the retry decision, with `job.id` returning the `job_name`. A callback that raises is logged and never changes the job's outcome. `_RQCompatJob` moved to `sqlery.core.execution.RQCompatJob` (the old name is kept as an alias).

- Dashboard authentication was skipped for any non-empty `root_path` (e.g. `uvicorn --root-path /x` behind a proxy). Auto-`inherit` now requires a real Starlette `Mount` (`scope["app_root_path"]`).
- A job whose dependency failed and has a pending retry (or was archived after retrying) was either failed immediately or blocked forever. Dependencies are now judged by their newest retry attempt in both modes. Retry jobs also keep their `dependencies`.
- Retry jobs dropped part of the original job spec. Standalone retries lost `retry_intervals`, `tags`, `meta`, `on_success_path`/`on_failure_path`, `result_ttl` and `failure_ttl`; Django retries lost `timeout_seconds`, `allow_parallel`, `tags`, `meta`, callbacks, TTLs and webhook settings (so a retried job could lose its timeout or tag limits). All are now carried over, and Django retries record `parent_job_id` so `AsyncResult` can follow them. Extra fields are only passed to backends whose `create_job` accepts them.
- Falsy task results (`0`, `False`, `[]`) were recorded as empty output (`result or ""`). Results are now stored via `serialize_result`: strings verbatim, `None` as empty, everything else as JSON (dict results appear in the dashboard as JSON rather than a Python repr).
- `TAG_CONCURRENCY_LIMITS` and `TAG_RATE_LIMITS` were never applied: the shared claim path defaulted both to `{}` and the standalone claim path skipped them entirely. Both now read config and enforce the limits (standalone serializes the check with a per-tag advisory lock on PostgreSQL).
- A tag-limited job at the head of the queue no longer starves everything behind it; the claim loop now walks a window of candidates instead of re-fetching the same blocked job.
- Tag counting no longer uses JSON `contains`, which is unsupported on SQLite (Django raised `NotSupportedError`) and wrong for PostgreSQL `json` columns in standalone mode.
- HTTP-trigger and serverless `job_id` paths now claim the job atomically (`DatabaseBackend.claim_job_by_id`) before executing, instead of running it without a claim; a duplicate trigger or an already-running job is rejected (409) rather than executed twice.
- Standalone `claim_job` now enforces `dependencies` (`depends_on`) and `ttl`, matching Django mode: a child no longer runs before its parent, and failed/missing dependencies fail the child.
- Standalone job retries now honour `retry_intervals`.

### Added

- Solar schedules (Celery's `solar`): a `cron_expression` of `solar:<event> <lat> <lon> [<offset>]` (events `sunrise`, `sunset`, `solar_noon`, `dawn`, `dusk`; offset like `-15m`, `+1h`) runs a scheduled task relative to the sun, e.g. `solar:sunset 40.7128 -74.0060 -15m`. `sqlery.solar.solar_spec(...)` builds one. It lives in the existing field, so there is no schema change and every scheduler/CLI/admin/API path that takes a cron string accepts it. Computed in pure standard-library Python (about a minute of accuracy, no new dependency); polar day/night skips to the next day the event occurs.
- `revoke(job_id, terminate=False)` / `AsyncResult.revoke()`: cancels a queued or scheduled job; with `terminate=True` also stops a *running* job. The request is a flag in the existing `termination_reason` column; the worker executing the job polls it (`REVOKE_POLL_INTERVAL`, default 2s, 0 disables), kills the job's process group, and marks it failed without retrying, so it works across hosts. `AsyncResult` gains a `REVOKED` state and `get()` raises `TaskRevokedError`. Running jobs are only interruptible under a `WorkerProcess` (daemon / `sqlery-worker`). New non-abstract `DatabaseBackend.request_job_revoke()` / `is_job_revoke_requested()`.
- Workflow primitives `chain`, `group`, `chord` (`from sqlery import chain, group, chord`) with task signatures `task.s(...)` / `task.si(...)` (immutable) and the `a.s() | b.s()` operator. Built on job `dependencies`, so no schema change and identical in Django and standalone modes. Non-immutable steps receive upstream results as leading arguments (chain: previous result; chord body or step after a group: list of results in order). A failed step fails everything downstream. `apply_async()` returns an `AsyncResult` (or `GroupResult`) with `.parent` linking back through the workflow. Jobs are created up front without a cross-job transaction.
- Celery-style task signals in `sqlery.core.signals`: `task_prerun`, `task_postrun`, `task_success`, `task_failure`, `task_retry`. `@signal.connect` / `connect(sender="module.task")`; receivers run in the process executing the job, and a receiver that raises is logged without failing the job. Fired from both the standalone and Django executors.
- `@job(soft_time_limit=N)` (core and Django): raises a catchable `SoftTimeLimitExceeded` inside the task after N seconds so it can clean up; the job's `timeout_seconds` remains the hard limit and still fires afterwards for the remaining time if the task keeps running.
- Celery-style retry policy on `@job` (core and Django): `autoretry_for=(Exc, ...)` (retry only matching exceptions; unset keeps retry-on-any), `retry_backoff_max`, `retry_jitter`, and `bind=True` which passes a `TaskContext` as the first argument (`self.request.retries`, `self.retry(exc=, countdown=)`). The `sqlery.core.retry.Retry` exception can also be raised directly. Retries still honour `max_retries`. The policy is read from the task function when a job fails, so it applies to enqueue, `delay`, `apply_async` and scheduled runs without extra storage.
- Celery-style results API: `job.apply_async(args, kwargs, countdown=, eta=, expires=, ...)` returns an `AsyncResult` (`sqlery.AsyncResult`) with `state`/`status`, `ready()`, `successful()`, `failed()`, `result`, `error`, `traceback` and a blocking `get(timeout=, propagate=)` that follows retry attempts. Available on core `@job` and Django `@job` functions. `expires` maps to the job `ttl`. Task arguments are passed explicitly, so option names can't collide with task kwargs. `.delay()`/`.enqueue()` are unchanged. New non-abstract `DatabaseBackend.get_latest_retry()` (both backends).
- `@job(tags=[...])` (core and Django decorators) and `create_job(tags=...)` on both backends, so `TAG_CONCURRENCY_LIMITS` / `TAG_RATE_LIMITS` are usable from the public API. Core `enqueue` takes the tags as `_tags` so a task's own `tags` kwarg is never shadowed. Staged (far-future) jobs keep their tags on promotion.
- Opt-in Celery-style retry tuning via config: `RETRY_BACKOFF_MAX` (cap) and `RETRY_JITTER` (full jitter). Defaults leave behaviour unchanged.

## [0.24.14] - 2026-08-13

### Fixed

- Daemon-free deployments no longer fail healthy jobs while reaping. The `ENABLE_DAEMON=False` reaper called `cleanup_dead_workers()`, which also sweeps "ghost" running jobs that no active worker row points at — but in this path jobs are claimed by a bare `manage.py run_jobs` process that never creates a worker row, so every genuinely running job matched the sweep. The reaper now marks dead worker rows only; orphaned jobs stay the job of `_reap_orphaned_running_jobs()`, which checks the claiming PID.
- `cleanup_dead_workers()` raised `FieldError` on every call and did nothing. It filtered on `current_job__isnull` after the field was demoted from a foreign key to a plain id column; it now filters on `current_job_id__isnull`.

## [0.24.13] - 2026-08-13

### Added

- `WORKER_ALIVE_TIMEOUT` (default 30 seconds) is now the single definition of worker liveness, shared by the dashboard counts, the row list, and the reapers via `is_worker_beating()`.
- `SUBPROCESS_TRIGGER_LOG` setting: subprocess-trigger children now write stdout/stderr to `BASE_DIR/tmp/sqlery_subprocess_trigger.log` instead of `DEVNULL`, so a crashed `run_jobs` child leaves a traceback somewhere. Set it to `""` to restore the old discard behaviour.

### Fixed

- The dashboard counted the workers of a destroyed container as active indefinitely. Worker counts read only the `status` column, and the sole writer of `status='dead'` runs in the daemon — so with `ENABLE_DAEMON=False` nothing ever reaped and rows stayed `idle`. Because `has_active_workers` feeds stuck-queue detection, a queue with zero real workers was reported healthy. Every count is now bounded by `last_heartbeat`. The 24h window on the row list is a "recently seen" filter only, and each row now carries its own `is_alive`.
- Running jobs whose claiming process died mid-job are now failed rather than left `running` forever. The PID check treats uncertainty as "still alive", so a job is only failed when its claimer is provably gone.
- Workers that stop beating are now marked dead in daemon-free deployments, where the daemon-only reaper never runs.

## [0.24.12] - 2026-08-13

### Changed

- Django compatibility is now exercised in CI on both Django 5.2 and Django 6.1, and package metadata advertises Django 6.1 support.

### Fixed

- Async shutdown no longer skips drain handling when shutdown starts immediately after a job is registered as in-flight.
- Job decorator wrappers preserve `__annotations__` on Python 3.14, where `functools.update_wrapper()` no longer copies them by default.

## [0.24.11] - 2026-08-13

### Fixed

- Postgres E2E test rail (issue #23): three independent root causes behind failing `test_mode_e2e[postgres-*]` cells and CI-runner orphan processes. Standalone http-trigger tests now send the required `X-Sqlery-Key` dashboard-auth header; standalone postgres cells get per-test queue isolation (previously shared one persistent queue with no cleanup); the Django http-trigger postgres cell is skipped with a documented reason (subprocess resolves the wrong test database). Both postgres CI steps now carry `--timeout=90` so a hang fails cleanly through pytest instead of forcing GitHub's external, uncatchable job cancellation.

## [0.24.10] - 2026-08-11

### Fixed

- SQLAlchemy backend job-completion fencing is now atomic: `mark_job_success`/`mark_job_failed` perform a CAS `UPDATE ... WHERE version = expected_version` instead of a read-then-commit check, closing the race left open in 0.24.9 (tracked as #17).
- `django-tasks` compatibility restored against its latest (API-breaking 0.10) release; CI no longer pinned below it.
- Daemon E2E tests no longer leak orphan worker processes, unblocking CI runs.
- Daemon no longer closes the Django persistent DB connection out from under the process.
- Dropped a legacy-table foreign key that was blocking PostgreSQL test-suite flush/teardown.

## [0.24.9] - 2026-08-10

### Fixed

- Job-completion fencing: workers now pass the post-claim `expected_version` to `mark_job_success`/`mark_job_failed`, so a stale worker whose job was reclaimed by the zombie sweep can no longer clobber the reclaimed job's outcome. A rejected late write raises `JobFencingError`, which is logged and discarded instead of double-recording a result. (Note: the SQLAlchemy backend's check is read-then-commit, not atomic CAS — tracked in #17.)
- Async worker retry-requeue: `arequeue_retry` is now implemented on both async backends. Previously a hard-coded `isinstance` check made retries a silent no-op on the Django async backend.
- Unawaited-coroutine guard: `mark_success`/`amark_success` on both backends now reject a coroutine object as a job result instead of persisting its repr.
- `select_for_update` call sites in the Django backend are guarded by `assert_in_atomic_block`, catching lock-less claims at the seam instead of failing silently on Postgres. The SQLite no-op claim path is exempt.

## [0.24.7] - 2026-07-14

### Fixed

- `JobExecutor._kill_worker_process()` (used by the admin "Stop Job" action) never reaped the killed child, leaking it as a permanent zombie under a PID-1 worker container. Its `os.kill(pid, 0)` liveness check couldn't tell a live process from an un-reaped zombie (both answer signal-0 successfully), so termination was never actually observed. Liveness now goes through `os.waitpid(pid, os.WNOHANG)`, which detects exit and reaps in the same call.

## [0.24.5] - 2026-07-08

### Fixed

- `QueuedJob.parent_job_id` widened from `IntegerField` (int4) to `BigIntegerField` (int8). It stores a 64-bit `_generate_job_id()` value, so when a failed job spawned a retry the INSERT raised `integer out of range` — swallowed by the worker's mark-failed handler, silently preventing retries. Postgres migration `0033` runs the real `ALTER COLUMN … TYPE bigint` (cascades across partitions); SQLite is state-only. SQLModel `parent_job_id` aligned to `BigInteger` for parity (the fastapi raw DDL was already `BIGINT`).

## [0.22.0] - 2026-05-25

### Added

- RQ compatibility layer (`sqlery.compat.rq`) now works in **standalone mode** — Django is no longer required to import or use it. The `Queue` wrapper, utility functions, and `Job`/`Worker` stubs route through the framework-agnostic `DatabaseBackend` ABC via `get_backend()`, with the Django fast-path preserved. Closes the strategic gap that left standalone RQ migrants without a migration path.
- `refresh_worker_heartbeat()` on the SQLAlchemy (standalone) backend — previously missing, leaving standalone worker heartbeats un-refreshed.
- IP/origin allowlist for internal trigger endpoints (`INTERNAL_ALLOWED_IPS` for Django, `SQLERY_INTERNAL_ALLOWED_IPS` for standalone), defense-in-depth on top of the existing HMAC check. Matches the real socket peer (`REMOTE_ADDR` / `request.client.host`), never `X-Forwarded-For`; loopback-only by default.

### Changed

- Daemon zombie detection is now **mode-agnostic**: the five-heuristic liveness logic lives once in `sqlery.core` and consumes structured data via two new backend methods (`get_running_jobs_for_liveness`, `fail_zombie_job`). Standalone mode now gets worker crash/zombie recovery that was previously Django-only. Django behavior is unchanged.
- Lambda/serverless handlers marked **EXPERIMENTAL** (docstring warnings, one-time runtime log per warm container, and doc callouts). Handler logic unchanged.
- Coverage gate `fail_under` raised 13 → 20.

### Fixed

- Corrected stale `__version__` in `sqlery/__init__.py` (was `0.13.0`, now tracks the real release version).

## [0.21.2] - 2026-05-25

### Added

- Scheduled tasks guide and migration index rename
- Dialect-aware atomic claiming: `SELECT FOR UPDATE SKIP LOCKED` for PostgreSQL, optimistic version-CAS for SQLite

### Fixed

- Auto-register workers on-demand to fix jobs waiting with idle workers
- Dashboard no longer spams console errors when session expires
- Dashboard no longer polls `/admin/sqlery/undefined` when config is missing

## [0.21.1] - 2026-05-18

### Fixed

- Wrap `claim_job` in `transaction.atomic` to fix worker crash-loop on PostgreSQL

## [0.21.0] - 2026-05-18

v0.21 milestone: 4 phases, 137 commits, full execution-mode parity across Django and standalone.

### Breaking

- **BREAKING:** Django minimum version raised to 5.2 LTS. Users on Django 4.2 must upgrade before installing this release. Required for native async ORM features used in the async worker rebuild.

### Added — Phase 01: Core Unification

- Framework-agnostic core in `sqlery.core` — all business logic decoupled from Django
- Standalone import verification in CI (no Django required for core imports)
- Dated deprecation stubs for moved modules (`worker_claiming.py`, `executor.py`)

### Added — Phase 02: Execution Modes

- `AsyncDatabaseBackend` ABC for hot-path async methods
- `DjangoAsyncBackend` — async implementation backed by Django 5.2 ORM
- `SQLAlchemyAsyncBackend` — async implementation backed by aiosqlite/asyncpg
- `AsyncWorker` rewrite with drain-with-deadline shutdown
- `shutting_down` job status for graceful drain
- Parametrized E2E test matrix for all existing execution modes
- Django daemon `--once` flag for CI/testing

### Added — Phase 03: Testing & CI

- PostgreSQL CI matrix with marker routing (`@pytest.mark.postgres`)
- Coverage gate with `fail_under` threshold
- Real-subprocess chaos tests for zombie detection, lease lifecycle
- Hypothesis-based property test infrastructure (stubbed pending pipeline rewrite)

### Added — Phase 04: Security & Cleanup

- `ALLOWED_TASK_MODULES` allowlist for task module imports (SEC-04)
- Dashboard auth middleware: `standalone` (API key), `disabled`, or `inherit` (SEC-01)
- SSRF defense for webhook URLs — blocks private IP ranges (SEC-02)
- CSRF protection restored on 10 state-changing admin endpoints (SEC-03)
- 22 backward-compatibility stubs date-stamped with `Remove after 2027-05-14`
- Drop-in RQ/django-tasks-scheduler compatibility declared permanent first-class feature

### Fixed

- `claim_next_job_with_queue_priority` arity mismatch (found during gap-closure)
- `worker_process.py:71` arity bug — pass `(worker, backend, queues)`
- Webhooks module moved to `django_sqlery/` with dated BC stub

## [0.20.4] - 2026-03-19

### Added

- Bulk archive scheduled jobs from dashboard

## [0.20.3] - 2026-03-19

### Fixed

- Increase job output truncation limits

## [0.20.2] - 2026-03-19

### Changed

- Finish top-level import migration in `core/`

## [0.20.1] - 2026-03-18

### Added

- Daemon watchdog and intervention API
- TTL retention tests, subprocess lifecycle tests

### Fixed

- Import cleanup in core modules

## [0.20.0] - 2026-03-16

Initial release — core library with Django and FastAPI integrations.

### Added

- Database-backed job queue with PostgreSQL and SQLite support
- `@job` decorator and `enqueue()` / `enqueue_at()` API
- Django integration: ORM models, admin, management commands
- Standalone integration: SQLModel models, FastAPI dashboard, Alembic migrations
- Daemon mode with worker pool, scheduler, heartbeats, and lease management
- Subprocess (fork-per-job) execution mode
- HTTP trigger execution mode
- Lambda/serverless handler
- Synchronous thread execution mode
- RQ and django-tasks-scheduler compatibility layers
- Typer-based CLI (`sqlery`, `sqlery-worker`, `sqlery-web`, `sqlery-daemon`, etc.)
- GitHub Actions CI with stress tests
- Job dependencies, retry with exponential backoff, rate limiting
- Cron-based scheduled tasks via `croniter`
- Optimistic locking (SQLite) and `SKIP LOCKED` (PostgreSQL) for concurrent claiming
