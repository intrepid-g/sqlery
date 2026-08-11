# REGRESSIONS

A log of bugs that reappeared — what broke, why, and how it was fixed.

Use this file to:
- Track recurring failure patterns
- Inform test prioritization
- Avoid reintroducing the same bug twice

## 2026-05-18 — TransactionManagementError in worker claim loop

**What broke:** Workers crash-loop every ~5s with `TransactionManagementError: select_for_update cannot be used outside of a transaction`. Job claiming was completely broken on PostgreSQL.

**Root cause:** When the claiming logic was promoted from `DjangoBackend.claim_job()` to the framework-agnostic `sqlery.core.claiming` module, the `transaction.atomic()` wrapper was lost. The old code (still visible as a comment block in backend.py) had it; the new delegation call did not. Both `get_claimable_jobs()` and `acquire_tag_locks()` use `select_for_update()`, which Django requires to be inside a transaction.

**Fix:** Wrapped the `claim_next_job_with_queue_priority()` call inside `with transaction.atomic():` in `DjangoBackend.claim_job()` — restoring the same pattern as the old commented-out code.

**Regression test:** `test_claim_job_runs_inside_transaction` in `tests/unit/test_django_backend.py`

**Inline comment:** `# REGRESSION 2026-05-18` at `src/sqlery/django_sqlery/backend.py:109`

**Validation:** All 7 TestEnqueueAndClaim tests pass including the new regression test.

## 2026-05-25 — Dashboard "Failed to fetch stats" console error on session expiry

**What broke:** While the SQLery admin dashboard was open, the browser console logged `Failed to fetch stats: Error: Failed to fetch stats` every ~3 seconds (the auto-refresh interval) after the Django session expired. The dashboard numbers stopped updating but kept polling indefinitely.

**Root cause:** `updateStats()` in `dashboard.js` treated only HTTP 429 as a non-error. Every other non-OK response (including the expected 403 from `@staff_required_json` when the session expired) was turned into a thrown `Error`, which the `catch` block logged as a hard failure on every refresh cycle. The same bug existed in `updateTasks()`, and `pollFeed()` silently returned on non-OK without stopping the interval.

**Fix:** In all three pollers (`updateStats`, `updateTasks`, `pollFeed`), handle 401/403 by clearing `autoRefreshInterval`, updating the refresh indicator, showing a toast (`"Session expired — Please reload and sign in again."`), and returning early. For other non-OK responses (e.g., 502/504), log a `console.warn` and skip the current tick without throwing, so polling continues. The old `throw new Error(...)` lines are preserved as commented-out code per project convention.

**Fix version:** v0.21.2

**Regression test:** `test_dashboard_session_fix.py` in `tests/unit/` — validates that the 401/403 guard clauses, interval clearing, toast calls, commented-out throws, and `console.warn` replacements all exist in the correct order in `dashboard.js`.

**Inline comment:** `// REGRESSION 2026-05-25` at `src/sqlery/django_sqlery/static/sqlery/js/dashboard.js` in `updateStats`, `updateTasks`, and `pollFeed`.

**Validation:** Re-read the modified `dashboard.js` code end-to-end to confirm the 401/403 guard clauses are placed before the JSON parse, the commented-out throw lines are preserved, and the `catch` blocks only surface genuine network/parse failures.

## 2026-05-25 — Dashboard polls `/admin/sqlery/undefined` when config is missing

**What broke:** The browser (or server logs) showed repeated requests to `/admin/sqlery/undefined` every ~3 seconds while the SQLery admin dashboard was open.

**Root cause:** If the inline `<script>` defining `DASHBOARD_CONFIG` failed to execute (e.g., blocked by a Content Security Policy, syntax error in a `{% url %}` result, or any other reason), `dashboard.js` created a fallback `{}`. Every auto-refresh function (`updateStats`, `updateTasks`, `pollFeed`) then called `fetch(undefined)` because `URLS.stats()`, `URLS.tasks()`, and `DASHBOARD_CONFIG.activityFeedUrl` were all `undefined`. The browser resolves `fetch(undefined)` relative to the current page (`/admin/sqlery/`), producing requests to `/admin/sqlery/undefined` (and `/admin/sqlery/undefined?limit=100`).

**Fix:** Added a `_urlOk()` helper that checks `typeof url === 'string' && url.length > 0`. Each of the three auto-refresh pollers now validates its URL before calling `fetch()` and returns early with a `console.warn` if the URL is missing. This turns an endless stream of 404s into a single console warning.

**Regression test:** `test_dashboard_undefined_url_fix.py` in `tests/unit/` — validates that `_urlOk` exists and is called before `fetch()` in `updateStats`, `updateTasks`, and `pollFeed`.

**Inline comment:** `// REGRESSION 2026-05-25: Dashboard polled /admin/sqlery/undefined` at `src/sqlery/django_sqlery/static/sqlery/js/dashboard.js:7`

**Validation:** All 36 dashboard tests pass (35 passed, 1 skipped), including the new regression test.

## 2026-05-25 — QueuedJob admin change page crashes on Django 6.0

**What broke:** Viewing a QueuedJob detail page at `/admin/sqlery/queuedjob/<id>/change/` raises `TypeError: args or kwargs must be provided` during template rendering of the "Execution History" readonly field.

**Root cause:** `runs_display()` in `QueuedJobAdmin` built an HTML table using f-strings and passed the result to `format_html(html_string)` with no format arguments. Django 6.0 tightened `format_html()` to require at least one positional arg or kwarg (enforced in `django/utils/html.py:137`).

**Fix:** Replaced `format_html(''.join(html_parts))` with `mark_safe(''.join(html_parts))` since the HTML is pre-constructed and no interpolation is needed.

**Regression test:** `test_runs_display_does_not_crash_with_execution_history` in `tests/test_admin.py`

**Inline comment:** `# REGRESSION 2026-05-25` at `src/sqlery/django_sqlery/admin.py:539`

**Validation:** All 12 admin tests pass. Manual verification that `runs_display` returns valid HTML for both populated and empty run histories.

## 2026-05-25 — Sync executor silently "succeeds" @async_job tasks without running them

**What broke:** When an `@async_job` (coroutine) task was processed by the sync `WorkerProcess`, the executor called the task, got back a coroutine object, and marked the job successful without ever awaiting it. The task body never ran. `QueuedJob.output` stored the coroutine repr string.

**Root cause:** The sync execution paths in both `_executor_impl.py` (Django mode) and `core/worker.py` (standalone mode) assumed `task_func(**kwargs)` always returns a plain value. For async tasks, calling the function returns a coroutine that must be awaited/run to completion.

**Fix:** After `result = task_func(...)`, check `inspect.iscoroutine(result)` and if true, run it via `asyncio.run(result)`. Applied at all three sync execution sites (1 in `_executor_impl.py`, 2 in `core/worker.py`).

**Regression test:** `test_sync_executor_awaits_async_task_coroutine` in `tests/test_executor.py`

**Inline comment:** `# REGRESSION 2026-05-25` at `src/sqlery/django_sqlery/_executor_impl.py:318`, `src/sqlery/core/worker.py:88`, `src/sqlery/core/worker.py:170`

**Validation:** Regression test passes; all 16 executor tests pass; direct validation confirms coroutine is detected and run to completion.

## 2026-06-14 — `select_for_update cannot be used outside of a transaction` (PG worker)
- **Broke:** `run_jobs` worker crashed after one job on PostgreSQL. `run_queue_workers` called `get_queued_jobs(...).exists()` to decide whether to spawn the next worker, but that queryset carries `SELECT ... FOR UPDATE SKIP LOCKED`; `.exists()` outside a transaction raises `TransactionManagementError` on PG (SQLite silently allows it). Surfaced via the partitioned sample project.
- **Fix:** added a non-locking `_executor_impl.TaskExecutor._has_more_queued_jobs()` existence probe and used it at the spawn-decision site (the lock is only needed when actually claiming).
- **Test:** `tests/test_more_jobs_probe_regression.py`.
- Prior related: 2026-05-18 (same class of bug in `backend.py` claim path).

## 2026-06-16 — `NoReverseMatch` for `admin:sqlery_queuedjob_changelist` (unified dashboard)
- **Broke:** `GET /admin/sqlery/` crashed (HTTP 500) rendering `unified_dashboard.html` — `Reverse for 'sqlery_queuedjob_changelist' not found`. Hit on Django 6.0 with the partitioned QueuedJob.
- **Root cause:** Phase 15 gave `QueuedJob` a composite primary key. Django 5.2+/6.0 raises `ImproperlyConfigured` for composite-PK models in admin, so `admin.site.register(QueuedJob, ...)` always fails and the changelist URL never exists. A bare `except Exception: pass` in `admin.py` swallowed the failure silently, while the dashboard template hard-reversed the (now nonexistent) URL.
- **Fix:** template (`unified_dashboard.html`) resolves the QueuedJob admin URLs via `{% url ... as var %}` (empty string on failure) and degrades the Success/Failed stat cards to plain `<div>` when absent; `admin.py` stops swallowing — it now catches `AlreadyRegistered` (autoreload guard) and logs the expected `ImproperlyConfigured` composite-PK case at INFO instead of `except Exception: pass`.
- **Test:** `test_unified_dashboard_renders_without_queuedjob_admin` in `tests/test_admin.py` (uses test URLconf `tests/urls_dashboard.py` mounting admin + sqlery; fails with `NoReverseMatch` pre-fix, 200 post-fix).
- **Validation:** reproduced the exact `NoReverseMatch` by registering `QueuedJob` directly (confirmed `ImproperlyConfigured: composite primary key`); test fails on unfixed template, passes after; full `tests/test_admin.py` green (13 passed).
- **Inline comment:** `REGRESSION 2026-06-16` at `templates/admin/sqlery/unified_dashboard.html` (stat cards + JS url block) and `django_sqlery/admin.py` registration block.

## 2026-06-16 — Dead workers never leave the dashboard (heartbeat 100s of hours stale)

- **What broke:** A worker whose `last_heartbeat` was hundreds of hours old (e.g. `418h17m ago`) kept appearing in the admin dashboard workers table, marked `idle`, with no way to age out. The user expects workers inactive for >24h to disappear.
- **Root cause:** `dashboard_stats()` (`src/sqlery/django_sqlery/views.py`) built the workers list with `Worker.objects.filter(status__in=['idle','busy'])` and had **no upper bound on heartbeat age**. A worker that died without flipping its status row stayed `idle` and rendered forever; the JS only colored the heartbeat cell red but never removed the row.
- **Fix:** added `.exclude(last_heartbeat__lt=now - timedelta(hours=24))` to the dashboard worker queryset so workers inactive >24h are omitted from the listing. (`views.py` ~line 777.)
- **Regression test:** `test_dashboard_excludes_workers_idle_more_than_24h` in `tests/test_dashboard_stale_workers.py` — creates a fresh idle worker and a 418h-stale idle worker, calls `dashboard_stats` as a staff user, asserts the stale one is absent and the fresh one present. Fails pre-fix (`stale-node` present), passes post-fix.
- **Inline comment:** `REGRESSION 2026-06-16` at `src/sqlery/django_sqlery/views.py` (dashboard workers queryset).
- **Validation:** confirmed the test fails against the unfixed query (removed the `.exclude(...)` line → `stale-node` reappears) and passes with the fix; `tests/test_serialize_worker.py` + `tests/test_admin.py` remain green (22 passed).
- **Note:** The 60s/30s heartbeat thresholds elsewhere (`get_worker_heartbeats`, `Worker.is_alive`) are separate liveness concerns and were intentionally left unchanged — the dashboard cutoff is a display concern (left a wish comment to make the 24h value configurable).

## 2026-08-08 — Unawaited-coroutine guard consolidated into a single choke point

- **What broke:** The 2026-05-25 fix for "sync executor silently succeeds `@async_job` tasks" patched three scattered call sites (`_executor_impl.py`, `core/worker.py` ×2). That pattern is regression-prone: any *future* executor call site (e.g. the async worker's `amark_success`) could reintroduce the same bug by forgetting the same `inspect.iscoroutine(...)` check.
- **Root cause (of the fragility, not a live bug):** there was no single place that *recorded* a job result where the coroutine check was structurally guaranteed to run — the check lived at the call sites instead of at the result-recording boundary. Auditing confirmed the async worker path (`amark_success` in both `django_sqlery/async_backend.py` and `fastapi_sqlery/async_backend.py`) bypasses `QueuedJob.mark_success()` entirely (raw `.aupdate()` / `update()` statement) and had **no** guard at all — an unawaited coroutine passed as `result` there would have been silently stored as a successful job.
- **Fix:** added `reject_unawaited_coroutine()` in `src/sqlery/core/utils.py` (uses `inspect.isawaitable`, raises `TypeError`). Called it at the top of the two actual result-recording choke points — `QueuedJob.mark_success()` in `src/sqlery/core/models.py` (standalone) and `src/sqlery/django_sqlery/models.py` (Django) — which is where every sync executor path (`_executor_impl.py`, `core/worker.py`, and any future sync call site) ultimately writes success. Also added the same guard directly in the two `amark_success()` implementations, since those bypass `mark_success()` and are otherwise unguarded. The three 2026-05-25 `asyncio.run()` fixes are left in place — they run the coroutine correctly, so the new guard never fires for them; it only fires for a *caller that forgot to await*.
- **Regression test:** `test_amark_success_rejects_unawaited_coroutine` in `tests/test_sqlalchemy_async_backend.py` — asserts the row is left in `status="running"` (not silently marked `success`) when an unawaited coroutine is passed, covering the async path that the 2026-05-25 fix never touched.
- **Inline comment:** `# REGRESSION 2026-08-08` at `src/sqlery/core/models.py`, `src/sqlery/django_sqlery/models.py`, `src/sqlery/django_sqlery/async_backend.py`, `src/sqlery/fastapi_sqlery/async_backend.py`.
- **Validation:** full suite (`pytest tests/ --ignore=tests/chaos/ --ignore=tests/integration/test_modes.py --timeout=60`) — 1095 passed, 89 skipped, 3 xfailed, same 1 pre-existing failure (`test_sc5_blast_radius_audit_zero_unaddressed`, missing planning file) and 8 pre-existing errors (`test_compat_rq_standalone.py`, `MockBackend` missing abstract method) as on the unmodified base — both confirmed unrelated by reverting the diff and re-running.
## 2026-08-08 — `select_for_update cannot be used outside of a transaction` (third occurrence, scheduled task claim)

- **Broke:** `DjangoBackend.claim_due_scheduled_task()` (the framework-agnostic path used by `core.scheduler_tasks.run_due_tasks()`) called `atomic_claim_job_queryset(...).get()` with no enclosing `transaction.atomic()`. Works on SQLite, raises `TransactionManagementError` on PostgreSQL. Same class of bug as 2026-05-18 (claim path) and 2026-06-14 (job-spawn probe) — audited while hardening against a third recurrence.
- **Root cause:** Same pattern each time: a `select_for_update()` call site gets promoted/rewritten into a new module and the `transaction.atomic()` wrapper doesn't travel with it. Nothing enforced the invariant, so it silently regressed a third time and only SQLite-only CI/local runs kept it green.
- **Fix:** Wrapped the query + `.get()` in `with transaction.atomic():` inside `claim_due_scheduled_task()`.
- **Structural guard (new):** Added `assert_in_atomic_block(caller)` in `src/sqlery/django_sqlery/db_compat.py`. It raises `RuntimeError` immediately — on SQLite too, not just Postgres — whenever `connection.in_atomic_block` is `False` at the point `select_for_update()` is about to be applied. Called from the two `select_for_update()` choke points: `atomic_claim_job_queryset()` and `DjangoBackend.acquire_tag_locks()`. This turns "only fails live against Postgres" into "fails on the first test run against any backend", so this failure class can no longer reintroduce silently.
- **Regression tests:** `test_claim_due_scheduled_task_runs_inside_transaction` and `test_select_for_update_guard_raises_outside_transaction` in `tests/unit/test_django_backend.py`.
- **Inline comment:** `REGRESSION 2026-08-08` at `src/sqlery/django_sqlery/backend.py:1211` (`claim_due_scheduled_task`).
- **Validation:** `tests/unit/test_django_backend.py` (84 passed), plus the full non-daemon suite (`tests/ --ignore=tests/chaos/ --ignore=tests/integration/test_modes.py`, 1095 passed / 90 skipped / 3 xfailed) — the only failures are the pre-existing `test_compat_rq_standalone` fixture gap and a missing-planning-doc test, both unrelated to this change (see `project_sqlery_test_env_gotchas`). No local PostgreSQL available, so the guard was validated by unit-testing `assert_in_atomic_block()` directly rather than a live Postgres run; it is backend-agnostic by construction (keys off `connection.in_atomic_block`, not `connection.vendor`) so it fires identically in CI's Postgres job.

## 2026-08-11 — postgres E2E rail: 3 of 8 `test_mode_e2e[postgres-*]` cells fail, CI runner killed (issue #23)

- **Noticed:** 2026-08-10, when PRs #20/#21/#22 unblocked CI step 10 ("Run @pytest.mark.postgres suite") for the first time — it had never actually executed before (an earlier `-x` failure always stopped the run first). Filed as issue #23.
- **What broke:** All 8 `tests/integration/test_modes.py::test_mode_e2e[postgres-*]` cells failed/errored, and the CI job was killed with `The operation was canceled.` plus orphaned `uv`/`pytest`/`python3` processes reported by the runner. Reproduced locally against a disposable Postgres container (`docker run postgres:15`) with `SQLERY_TEST_PG_URL` set.
- **Root causes (three independent bugs, not one generic "process leak"):**
  1. `[postgres-django-http-trigger]`: `get_manage_py_path()` raises because no `manage.py` exists at `BASE_DIR` in the test environment. Adding a test-only `manage.py` gets past that error but does NOT fix the cell — the spawned `python manage.py run_jobs --once` subprocess re-imports `tests.settings` from scratch and resolves `DATABASES['NAME']` from the raw `SQLERY_TEST_PG_URL` env var, missing pytest-django's runtime swap to the `test_<name>` database, so it claims against the wrong database and the enqueued job stays invisible to it. Confirmed by direct repro (added the file, reran, job still stuck `queued`); reverted the file.
  2. `[postgres-standalone-http-trigger]`: the FastAPI app's `DashboardAuthMiddleware` (SEC-01) wraps every route including `/trigger`, requiring the `X-Sqlery-Key` header (documented as intentional in `docs/SECURITY.md`). The test driver never sent it, so every request 401'd before reaching the trigger's own HMAC-signature check, and the job sat `queued` until its 30s poll timeout.
  3. `[postgres-standalone-subprocess]` and `test_async_e2e_standalone_pg`: standalone-mode postgres cells connect directly to `SQLERY_TEST_PG_URL` with zero per-test isolation (unlike Django cells, which get automatic flushing from `transactional_db`). `claim_job()` claims the OLDEST `queued` row in the target queue — a stale row from an earlier cell or a previous interrupted run gets claimed instead of the row the current test just enqueued, so the current job never reaches `success` within its poll window.
  - Separately, `.github/workflows/test.yml`'s "Run @pytest.mark.postgres suite" and "Run standalone-mode parity suite with PostgreSQL" steps had no `--timeout`, unlike every other pytest invocation in the same workflow. A hanging cell (confirmed locally: the daemon cells run far longer than any other cell against this Postgres setup) would consume the whole job budget until GitHub's own external cancellation force-kills the process tree — bypassing the in-process `finally:` cleanup in `daemon.py`/`worker_pool.py` that normally reaps tracked child processes. That external, uncatchable kill is what produces the orphan-process report; a catchable pytest-timeout was verified (locally) to let that same cleanup run and reap children cleanly instead.
- **Fix:**
  - `tests/integration/conftest.py::_drive_http_trigger_standalone` now sets `SQLERY_DASHBOARD_API_KEY` and sends the matching `X-Sqlery-Key` header, per the documented dashboard-auth contract.
  - `tests/integration/conftest.py::pytest_collection_modifyitems` now skips `(http-trigger, django, postgres)` with a stated reason (mirrors the existing SQLite daemon/http-trigger skip pattern) — this cell cannot pass through the pytest-django test-db-name swap without much deeper harness surgery.
  - `tests/integration/conftest.py` adds an autouse `_isolate_standalone_postgres_queue` fixture that truncates `sqlery_queued_job` before every `@pytest.mark.postgres` test in this directory, restoring the isolation Django cells get for free.
  - `.github/workflows/test.yml` adds `--timeout=90` to both previously-unbounded postgres steps.
- **Regression test:** the fixed cells themselves — `test_mode_e2e[postgres-standalone-http-trigger]`, `test_mode_e2e[postgres-standalone-subprocess]`, `test_async_e2e_standalone_pg` — now pass reliably (verified 2x back-to-back with no manual DB cleanup in between); `test_mode_e2e[postgres-django-http-trigger]` reports `SKIPPED` with a clear reason instead of failing.
- **Inline comment:** `Issue #23` at `tests/integration/conftest.py` (`_drive_http_trigger_standalone`, `_clear_standalone_pg_queue`, `skip_django_http_trigger_pg`) and `.github/workflows/test.yml` (both postgres steps).
- **Validation:** ran the full non-daemon postgres suite (`tests/integration/test_modes.py tests/integration/test_async_e2e.py -m postgres -k "not daemon"`) twice back-to-back against a local Postgres 15 container with no manual cleanup between runs — both runs: 6 passed, 1 skipped, 1 xfailed (pre-existing, unrelated `test_async_e2e_django_pg`), 0 failed; `ps aux` showed zero leftover `worker_runner`/subprocess processes after each run. Also confirmed no regressions on the SQLite rail: `tests/integration/ tests/unit/test_dashboard_auth.py tests/test_http_trigger.py tests/test_triggers.py -m "not postgres"` — 63 passed, 4 skipped, 0 failed. The `daemon` postgres cells were excluded from these runs — they are extremely slow/hang against this local Docker Postgres setup regardless of this fix (pre-existing, macOS-local; the issue's own text attributes this to local environment noise not reproduced on Linux CI). Separately confirmed: forcing a daemon cell to hit pytest's `--timeout` leaves zero orphan processes afterward, supporting the `--timeout` fix for the CI-cancellation symptom.
