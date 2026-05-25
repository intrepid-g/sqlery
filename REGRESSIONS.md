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
