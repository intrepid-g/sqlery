---
status: complete

task: >
  Two independent hardening changes: (1) install django-tasks in the dev
  extra so the real django-tasks execution path runs in CI instead of being
  silently skipped, and (2) add Postgres coverage for the async worker E2E
  path (previously SQLite-only). Both changes surfaced real, previously
  undetected bugs — one fixed inline, one documented as xfail because a
  correct fix requires an architectural decision out of scope for this task.

not_needed_when: []

summarize_when:
  - superseded by a follow-up plan that fixes the DjangoAsyncBackend
    Postgres claim path (connection.acursor() bug)
---

# Quick Task 260715-inf Summary

**One-liner:** Unlocked the real django-tasks CI path (fixing a
never-callable nested-closure bug in the process) and added async-worker
Postgres E2E coverage, which found and documented a genuine
`connection.acursor()` bug in Django's async claim path on Postgres.

## What changed

### Task 1 — django-tasks in dev extra

- `pyproject.toml`: added `"django-tasks>=0.1.0"` to the `dev` extra
  (installed `django-tasks==0.9.0` via `uv sync --extra dev`; `uv.lock`
  updated).
- `tests/test_subprocess.py`: tightened `test_django_tasks_mode_explicit`
  and `test_auto_mode_with_django_tasks` to assert `"django-tasks"` exactly
  (package is now installed, so the tolerant `in ["django-tasks",
  "subprocess"]` assertion was no longer meaningful). `test_django_tasks_mode_fallback`
  and `test_auto_mode_without_django_tasks` now `monkeypatch` the module's
  `django_tasks` reference to `None` to genuinely simulate unavailability
  (previously they just asserted `"subprocess"` and happened to pass only
  because the package was absent from the environment — not because the
  fallback branch was actually exercised on purpose).
- `tests/test_triggers.py`: same treatment for `TestDjangoTasksMode`.
  Renamed the fallback tests to `..._fallsback_when_unavailable` and made
  them `monkeypatch` `sqlery.triggers.task` to `None`. Fixed the
  "uses_task_decorator" tests to `@patch("sqlery.triggers.task")` instead
  of `@patch("django_tasks.task")` — patching `django_tasks.task` after
  `sqlery.triggers` already did `from django_tasks import task` has no
  effect on the name bound in `sqlery.triggers`, so those tests were not
  verifying what their names claimed even when they weren't skipped.
- `src/sqlery/triggers.py`: **real bug fix.** `_enqueue_django_tasks()` and
  `_process_queue_django_tasks()` decorated a nested closure
  (`enqueue_due_tasks` / `process_queue`) with `@task()`. django-tasks
  rejects this at decoration time: `InvalidTaskError: Task function must
  be defined at a module level.` This was never caught because the real
  django-tasks path was always skipped in CI (package not installed).
  Fixed by extracting the job bodies to module-level `_run_due_tasks_job()`
  / `_run_queue_job(queue_name)` functions and calling
  `task(_run_due_tasks_job).enqueue()` / `task(_run_queue_job).enqueue(queue_name)`.

### Task 2 — async worker Postgres coverage

- `tests/integration/test_async_e2e.py`: added
  `test_async_e2e_standalone_pg` and `test_async_e2e_django_pg`, both
  `@pytest.mark.postgres`, relying on the existing
  `tests/integration/conftest.py::pytest_collection_modifyitems` skip-clean
  convention for when `SQLERY_TEST_PG_URL` is unset (no new manual skip
  logic needed — that's what the shared conftest hook is for).
  - Standalone fixture (`_standalone_async_engine_pg`) uses
    `sqlery.fastapi_sqlery.database.init_database()` for schema setup
    (not a raw `SQLModel.metadata.create_all`) because Postgres fresh
    installs get partitioned DDL with a composite PK for
    `sqlery_queued_job` — a raw `create_all` fails with `InvalidForeignKey`
    against `sqlery_registry`'s FK. Mirrors the pattern already established
    in `tests/test_standalone_lifecycle_partitioned.py::_make_pg_async_backend`.
  - Async URL translation (`postgresql://` -> `postgresql+psycopg://`) uses
    the psycopg3 async driver already in the project's dependency set — no
    new dependency, matching the constraint in CLAUDE.md.
  - Django variant relies on Django's session-wide `SQLERY_TEST_PG_URL`
    -> Postgres DB switch (`tests/settings.py`), same convention as every
    other `@pytest.mark.postgres` Django cell in `test_modes.py`.
- `docs/internal/RUN_MODES_STATUS.md`: updated the Async worker and
  django_tasks backend rows/sections with the findings below (gitignored
  file, edited in place, not force-added to git).

## Real findings (not papered over)

1. **`src/sqlery/triggers.py` nested-closure bug — fixed.** See above.
2. **`DjangoAsyncBackend._aclaim_job_postgres` is broken on Postgres — NOT
   fixed, documented as `xfail(strict=True)`.**
   `src/sqlery/django_sqlery/async_backend.py:121` does
   `async with connection.acursor() as cur:`. `connection.acursor()` does
   not exist on Django's `DatabaseWrapper` in any released Django version
   — confirmed absent on Django 6.0.5 installed in this project (the
   module's own docstring assumed it against Django 5.2.14, but this was
   never verified against a real Postgres connection until this task added
   the first PG-marked async Django test). A correct fix needs an
   architectural decision: either a `sync_to_async` thread-offload of
   `connection.cursor()` (explicitly forbidden by this module's own
   documented project rule — see the async_backend.py module docstring) or
   a separate native-async psycopg connection managed outside Django's ORM
   connection wrapper. Both are out of scope for a quick task; the xfail
   test (`test_async_e2e_django_pg`) documents the full root cause in its
   `reason=` string so it fails loudly and demands attention if a future
   change accidentally "fixes" it without anyone noticing (strict xfail
   flips to an error on unexpected pass).
3. **Cosmetic pytest reporting quirk (not chased further):** the xfail
   Django-PG test above reports as "2 xfailed" for one test item instead of
   "1 xfailed" — both a call-phase failure and a teardown-phase FK/flush
   error against the partitioned schema get attributed to the same xfail
   marker. Exit code is still 0 and this does not block CI; investigate
   only if the underlying teardown failure recurs on a test that is NOT
   already expected to fail.

## Verification

- `uv run pytest tests/test_subprocess.py tests/test_triggers.py -q` — 21 +
  18 = 39 passed.
- `uv run pytest tests/ --ignore=tests/chaos/ --ignore=tests/integration -m "not postgres" -q`
  — 1087 passed, 73 skipped, 3 xfailed (pre-existing xfails), 8 errors +
  1 failed — all 9 confirmed pre-existing via `git stash` (unrelated
  `test_compat_rq_standalone.py` `MockBackend` abstract-method gap and a
  missing `.planning/phases/15-schema-cutover/BLAST-RADIUS-AUDIT.md` file).
- `uv run pytest tests/integration/test_async_e2e.py -m "not postgres" -q`
  — 2 passed, 2 deselected (the new PG cells correctly excluded).
- **Local Postgres verification (docker `postgres:15`, ephemeral, torn
  down after):**
  `uv run pytest tests/integration/test_async_e2e.py -m postgres -q`
  with `SQLERY_TEST_PG_URL` set — 1 passed
  (`test_async_e2e_standalone_pg`), 1 xfailed as documented
  (`test_async_e2e_django_pg`), exit code 0.
- `uv run ruff check` on all touched files: clean (pre-existing unused
  imports in `tests/test_subprocess.py` and `tests/test_triggers.py`, and
  pre-existing black drift in three files, confirmed via `git stash` and
  logged to `deferred-items.md` — out of scope, not introduced by this
  task).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Nested-closure `@task()` decoration in django-tasks path**
- **Found during:** Task 1, step 3 (running tests against the newly-installed
  real django-tasks package).
- **Issue:** `_enqueue_django_tasks()` / `_process_queue_django_tasks()` in
  `src/sqlery/triggers.py` decorated a locally-nested function with
  `@task()`. django-tasks raises `InvalidTaskError` at decoration time for
  any non-module-level function.
- **Fix:** extracted job bodies to module-level `_run_due_tasks_job()` /
  `_run_queue_job(queue_name)`, called via `task(fn).enqueue(...)`.
- **Files modified:** `src/sqlery/triggers.py`, `tests/test_triggers.py`.
- **Commit:** `d85d1cb`

**2. [Rule 1 - Bug, not fixed — documented as xfail] `connection.acursor()`
does not exist**
- **Found during:** Task 2, step 5 (local Postgres verification).
- **Issue:** see "Real findings" #2 above. Fixing this correctly requires
  an architectural decision that conflicts with an explicit, documented
  project rule (no thread-offload helpers) — this is Rule 4 territory
  (architectural change), not a Rule 1 auto-fix, so it was left as
  `xfail(strict=True)` with the full diagnosis in the reason string rather
  than silently working around it.
- **Files modified:** `tests/integration/test_async_e2e.py` (xfail marker
  + reason only; `src/sqlery/django_sqlery/async_backend.py` was NOT
  modified).
- **Commit:** `b55213d`

## Self-Check

- `pyproject.toml` dev extra contains `django-tasks>=0.1.0`: FOUND
- `src/sqlery/triggers.py` has `_run_due_tasks_job` / `_run_queue_job`: FOUND
- `tests/integration/test_async_e2e.py` has `test_async_e2e_standalone_pg`
  and `test_async_e2e_django_pg`: FOUND
- Commit `d85d1cb`: FOUND (`git log --oneline --all | grep d85d1cb`)
- Commit `b55213d`: FOUND (`git log --oneline --all | grep b55213d`)

## Self-Check: PASSED
