# Phase 02 — Plan-Checker Fix Log

Applied 2026-05-13 in response to plan-checker review. Surgical edits only; plan structure preserved.

## Fix 1 (BLOCKER B1) — Lambda smoke asserts DB lifecycle, not return value
- **Plan:** 02-08
- **File:** `.planning/phases/02-execution-modes/02-08-PLAN.md`
- **What changed:**
  - `must_haves.truths` DMOD-04 and SMOD-04 entries: replaced "asserts a job lifecycle" / "smoke test passes" with explicit DB-row lifecycle assertions (status in {running, finished}).
  - Task 3 acceptance test rewritten: removed `assert result["processed"]`; added `assert QueuedJob.objects.get(id=job.id).status in {"running", "finished"}` (Django) plus a SQLModel-equivalent mandate for the standalone twin.
  - Added explicit instruction that `tests/integration/test_lambda_django.py` reuses `integration_setup` from Plan 02-07 (or replicates its `DJANGO_SETTINGS_MODULE` wiring).
  - Added a fixture-asymmetry note: Django twin uses `integration_setup`, standalone twin scrubs `DJANGO_SETTINGS_MODULE` — both behaviors are intentional, called out in test-module docstrings.
- **Lines touched (approx):** frontmatter L29–L31, Task 3 body L162–L185 (whole task body replaced), Task 3 `<done>` L185.

## Fix 2 (W1) — AsyncDatabaseBackend ABC adds aget_status / aget_job; name-based verification
- **Plan:** 02-03
- **File:** `.planning/phases/02-execution-modes/02-03-PLAN.md`
- **What changed:**
  - `must_haves.truths`: removed "12-15 hot-path methods" wording; added explicit mention of `aget_status` and `aget_job` for the 02-08 Task 4 async E2E harness.
  - `<interfaces>` method list: inserted `aget_status(self, job_id) -> str | None` and `aget_job(self, job_id) -> QueuedJob | None` with comments tying them to 02-08 Task 4.
  - `<verification>` block: replaced brittle `grep -c "@abstractmethod" ... == 14` with a name-based positive check (Python one-liner asserting the required-method set is a subset of `AsyncDatabaseBackend.__abstractmethods__`).
- **Lines touched (approx):** frontmatter L17, `<interfaces>` L58–L66, `<verification>` L99–L101.

## Fix 3 (W2) — Drain-with-deadline: amark_shutting_down BEFORE the race; assert transient state
- **Plan:** 02-06
- **File:** `.planning/phases/02-execution-modes/02-06-PLAN.md`
- **What changed:**
  - `must_haves.truths` shutdown bullet: explicitly states `await backend.amark_shutting_down(job_id)` is called BEFORE the `asyncio.wait(...)` race.
  - `<interfaces>` race shape: re-ordered so the `amark_shutting_down` call appears as the FIRST line of the shutdown sequence, with comment marking it.
  - Task 2 `<behavior>`: behavior bullet now states the ordering explicitly.
  - Task 2 `<action>`: instructs implementer to ensure the call precedes the race.
  - Task 2 test plan: deadline-wins test must use a second async session/connection to peek the row's `status` between dispatch and deadline-resolution and assert it briefly equals `'shutting_down'`. Job-wins path explicitly excluded from this requirement (transient state may not be observable there).
  - `<verification>`: added `grep -c "amark_shutting_down" ... >= 1`.
- **Lines touched (approx):** `must_haves.truths` L22, `<interfaces>` L67–L82, Task 2 body L120–L143, `<verification>` L173.

## Fix 4 (W3) — Parametrized harness: real smoke + Django `--once` flag pre-check
- **Plan:** 02-07
- **File:** `.planning/phases/02-execution-modes/02-07-PLAN.md`
- **What changed:**
  - `<context>` block: added `@src/sqlery/django_sqlery/management/commands/daemon.py` so the executor reads the current state of the Django daemon command.
  - Task 1 action: added SUB-STEP 1 — verify whether `python manage.py daemon --once` exists; if missing, either add the flag (pass-through) or switch the Django harness branch to `python -m sqlery.core.cli daemon --once` with env-set `DJANGO_SETTINGS_MODULE`. This is the FIRST sub-step of Task 1.
  - Task 1 `<verify>`: replaced `--collect-only | grep -q "no tests collected"` (which only proved the file imports) with a real construction smoke that builds the harness for `(daemon, django, sqlite)` and asserts `harness is not None and harness.backend is not None`. Requires a `_build_harness(...)` factory helper exported from conftest.
  - Task 1 `<done>`: updated to mention the wired-backend assertion and the `--once` flag decision.
- **Lines touched (approx):** `<context>` L58 added, Task 1 action L86–L101 (full rewrite), `<verify>` L102–L104, `<done>` L105.

## Fix 5 (W4) — Document SQLite version-backfill + Django 5.2 transaction.aatomic [ASSUMED] block
- **Plan:** 02-04
- **File:** `.planning/phases/02-execution-modes/02-04-PLAN.md`
- **What changed:**
  - Added a new `<assumptions>` block between `<objective>` and `<execution_context>` containing two [ASSUMED] notes:
    1. **version backfill:** Legacy `QueuedJob` rows with `version IS NULL` will cause silent CAS failure. Backfill `UPDATE sqlery_queued_job SET version = 1 WHERE version IS NULL;` either in the test harness setup or as part of Plan 02-02. Includes verification step (grep migrations + check schema NULL allowance).
    2. **RESEARCH §A2 — transaction.aatomic:** If Django 5.2 ships `transaction.aatomic()`, use it; if not, the "no `sync_to_async`" rule forces either (preferred) raw `acursor()` BEGIN/COMMIT, or `transaction.atomic()` synchronously inside async (blocks event loop — trade-off documented).
  - Task 1 action gained two PRE-STEPs: (1) confirm `transaction.aatomic` availability via a Python one-liner, (2) apply version backfill in the test fixture if exercising legacy rows.
  - Task 1 `<done>`: now requires [ASSUMED] choices to be documented in the module docstring.
- **Lines touched (approx):** new `<assumptions>` block at L45–L60, Task 1 action L107–L113 PRE-STEPs added, Task 1 `<done>` L130.

All plan-checker fixes applied. Phase 02 plans ready for /gsd-execute-phase.
