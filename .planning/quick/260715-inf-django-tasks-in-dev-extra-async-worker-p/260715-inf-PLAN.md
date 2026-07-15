---
status: complete

task: >
  Two independent hardening changes to sqlery's honest-status test infra:
  (1) unlock the real django-tasks execution path in CI by adding
  django-tasks to the dev extra and tightening tolerant test assertions,
  (2) add Postgres coverage for the async worker E2E path, which currently
  only runs on SQLite.

success_criteria:
  - django-tasks>=0.1.0 added to dev extra in pyproject.toml, uv.lock updated
  - test_subprocess.py / test_triggers.py django-tasks assertions tightened,
    no mirrored-bug tests, explicit ImportError-simulation fallback tests
  - tests/integration/test_async_e2e.py has postgres-marked coverage
    following the existing SQLERY_TEST_PG_URL skip-clean convention
  - docs/internal/RUN_MODES_STATUS.md updated honestly for both rows
  - SUMMARY.md + STATE.md updated

not_needed_when:
  - both changes committed and verified locally to the extent possible

summarize_when:
  - both tasks committed
---

# Quick Task 260715-inf: django-tasks dev extra + async worker PG coverage

## Task 1 — unlock real django-tasks path in CI

1. Add `"django-tasks>=0.1.0"` to the `dev` extra in `pyproject.toml`.
2. Tighten tolerant assertions in `tests/test_subprocess.py` and
   `tests/test_triggers.py`: when django-tasks IS importable, expect
   `"django-tasks"` exactly; add/keep a fallback test that simulates
   unavailability via monkeypatch and expects the fallback behavior.
3. `uv sync --extra dev`, run `uv run pytest tests/test_subprocess.py
   tests/test_triggers.py -x -q`. Fix minimally if trivial; otherwise xfail
   with reason and report honestly.
4. Commit pyproject.toml + uv.lock + test files together:
   `(feat): install django-tasks in dev extra`

## Task 2 — async worker Postgres coverage

1. Follow the existing `SQLERY_TEST_PG_URL` skip-clean pattern (see
   `tests/test_standalone_lifecycle_partitioned.py` for the async-engine PG
   URL translation convention: `postgresql+psycopg://`).
2. Add postgres-marked coverage to `tests/integration/test_async_e2e.py`.
3. Guard: skip cleanly when `SQLERY_TEST_PG_URL` unset.
4. Run `uv run pytest tests/integration/test_async_e2e.py -m "not postgres" -q`
   locally (must stay green); run postgres-marked variants only if local PG
   is available.
5. Commit: `(test): add postgres coverage for async worker`

## Wrap-up

- Update `docs/internal/RUN_MODES_STATUS.md` rows for `django_tasks backend`
  and `Async worker` (honest, gitignored file, edit but do not force-add).
- Write SUMMARY.md, update STATE.md Quick Tasks Completed table.
- Commit planning artifacts: `(chore): record quick task 260715-inf`.
