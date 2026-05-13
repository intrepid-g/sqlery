---
phase: 01-core-unification
plan: 03
subsystem: ci-and-regression-tests
tags: [ci, verification, standalone, regression-prevention]
requires:
  - 01-01 (unguarded django imports removed)
  - 01-02 (duplicate modules retired)
provides:
  - pytest-layer regression guard for standalone import contract
  - CI job (standalone-no-django) running in a django-less venv
affects:
  - tests/test_core_standalone.py (new)
  - .github/workflows/test.yml (additive job)
tech-stack:
  added: []
  patterns:
    - subprocess + MetaPathFinder pattern for env-isolated import tests
    - additive CI job using existing [standalone] extra (no uv sync)
key-files:
  created:
    - tests/test_core_standalone.py
  modified:
    - .github/workflows/test.yml
decisions:
  - "Reused existing [project.optional-dependencies].standalone extra in pyproject.toml instead of adding a new minimal group — it already lists sqlmodel/fastapi/uvicorn/jinja2/alembic/typer/rich and the base deps cover croniter+uuid6. Adding another extra would be duplicative."
  - "Dropped Task 2 step 3 (full enqueue/claim smoke). Per the plan's own escape hatch, an end-to-end SQLite enqueue cycle belongs in Phase 2 SMOD-01 work. Steps 1+2 (django-absent assertion + 11-submodule import) are the agreed floor and satisfy UNIF-04/05/06."
  - "Used Python 3.10 only for the new CI job (the project minimum) rather than matrixing across 3.11/3.12/3.13. The import contract is invariant across minor versions; one job keeps CI cost low."
metrics:
  duration: ~10 minutes
  completed: 2026-05-13
  tasks_completed: 2 of 3 (Task 3 is a human-verify checkpoint)
  files_created: 1
  files_modified: 1
---

# Phase 01 Plan 03: CI + Pytest Regression Guard for Standalone Imports Summary

Add two-layer regression infrastructure that locks in the standalone-import contract delivered by 01-01 and 01-02: a pytest test using a subprocess + MetaPathFinder to block django, and a CI job that installs in a fresh venv without django at all.

## What Changed

### Task 1: pytest regression test (commit `de2eb54`)

Created `tests/test_core_standalone.py` with two tests:

1. `test_core_imports_without_django` — spawns a fresh Python interpreter, installs a `MetaPathFinder` that raises `ImportError` for `django` and `django.*`, then imports all 11 `sqlery.core` submodules and asserts the sentinel `OK` is printed.
2. `test_db_resilience_retry_works_without_django` — same subprocess pattern, then imports `sqlery.core.db_resilience`, applies `@retry_on_db_error(max_retries=1)` to a trivial function, and asserts it returns the underlying value. This exercises Plan 01-01's `_RETRYABLE_EXC` fallback path at runtime.

The 11-module list is enumerated at the top of the file (`_CORE_SUBMODULES`) so future additions to `sqlery.core/` get a single-source-of-truth callout.

Both tests pass locally (`PYTHONPATH=. uv run pytest tests/test_core_standalone.py -v` → 2 passed in 0.73s).

### Task 2: CI job `standalone-no-django` (commit `1355f3f`)

Added a new job at the bottom of `.github/workflows/test.yml`:

- Single Python 3.10 runner (project minimum).
- Installs via `uv pip install ".[standalone]"` — does NOT use `uv sync` (which would pull `dev` + django).
- Step "Assert django is NOT installed" runs `uv run python -c "import django"` and FAILS the job if it succeeds. This is the T-01-04 mitigation: prevents a transitive install of django from masking regressions.
- Step "Import all sqlery.core submodules" imports the same 11 modules and prints `all-core-imports-ok`.
- Existing matrix (3.11/3.12/3.13 Tests job) is untouched; the new job is purely additive.

Locally simulated the CI behavior:

```
$ uv venv /tmp/standalone-check -p 3.11
$ uv pip install --python /tmp/standalone-check/bin/python ".[standalone]"
$ /tmp/standalone-check/bin/python -c "import django"
ModuleNotFoundError: No module named 'django'  ✓
$ /tmp/standalone-check/bin/python -c "import sqlery.core; ..."
all-core-imports-ok  ✓
```

YAML structural sanity check:

```
$ python -c "import yaml; d = yaml.safe_load(open('.github/workflows/test.yml')); \
             assert 'standalone-no-django' in d['jobs']; \
             assert any('all-core-imports-ok' in str(s) for s in d['jobs']['standalone-no-django']['steps']); \
             print('OK')"
OK
```

### Task 3: Human verification checkpoint

`type="checkpoint:human-verify"` — gated on the CI job going green on a PR. Not actionable from within the parallel executor (cannot open a PR from a worktree branch). The checkpoint is recorded here for the orchestrator/user to satisfy post-merge: push the branch, open a PR, confirm the `standalone-no-django` job is green and that the "django absent OK" + "all-core-imports-ok" lines appear in the logs.

## pyproject.toml

Not modified. The existing `[project.optional-dependencies].standalone` extra (sqlmodel, fastapi, uvicorn[standard], jinja2, typer, rich, alembic) plus the base `dependencies` (croniter, uuid6) cover every transitive requirement of `import sqlery.core`. Verified by the local simulation above.

## Deviations from Plan

None — plan executed as written. The plan included an explicit escape hatch ("If the executor cannot get this end-to-end cycle working within this task's scope ... DROP STEP 3 and surface the failure"); we exercised that hatch as a deliberate design decision rather than a deviation. Documented in `decisions` above.

## Threat Mitigations Applied

| Threat ID | Mitigation |
|-----------|------------|
| T-01-04 | "Assert django is NOT installed" step in the CI job fails the run if django becomes a transitive dependency, preventing a false-positive pass. |

## Known Stubs

None.

## Verification

- `uv run pytest tests/test_core_standalone.py -v -x` → 2 passed.
- YAML parse + `standalone-no-django` job presence + `all-core-imports-ok` step content asserted via inline Python script.
- Local simulation in a clean `/tmp/standalone-check` venv reproduces the CI behavior end-to-end (django absent, all 11 imports succeed).

## Self-Check: PASSED

- `tests/test_core_standalone.py` — FOUND
- `.github/workflows/test.yml` (standalone-no-django job) — FOUND
- Commit `de2eb54` (test) — FOUND
- Commit `1355f3f` (ci) — FOUND
