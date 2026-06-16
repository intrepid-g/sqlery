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
