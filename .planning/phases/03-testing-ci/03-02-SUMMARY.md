---
phase: 03-testing-ci
plan: 02
subsystem: ci
tags: [ci, github-actions, postgres, markers, test-12, test-11]
requires: []
provides:
  - "CI runs on `main` branch (TEST-12 closed)"
  - "`postgres` pytest marker registered (TEST-11 scaffolded)"
  - "CI rail for `pytest -m postgres` against PG service container"
affects:
  - .github/workflows/test.yml
  - pyproject.toml
tech-stack:
  added: []
  patterns:
    - "pytest marker registration in [tool.pytest.ini_options]"
    - "Pytest mode-deselect via `-m \"not postgres\"` in SQLite jobs"
key-files:
  created: []
  modified:
    - .github/workflows/test.yml
    - pyproject.toml
decisions:
  - "Tolerate pytest exit code 5 (no tests collected) in postgres CI step until Plan 03-07 tags tests"
  - "Use `-m \"not postgres\"` (not `--ignore`) so SQLite jobs deselect PG tests by marker, allowing files to be shared"
metrics:
  duration_minutes: 4
  completed: 2026-05-14
requirements: [TEST-11, TEST-12]
---

# Phase 03 Plan 02: CI Triggers + Postgres Marker Scaffold Summary

**One-liner:** Flipped CI triggers from `master` to `main` and registered the `postgres` pytest marker with a PG-service CI step that tolerates empty collection until Plan 03-07.

## What Was Built

### Task 1: Register `postgres` pytest marker (commit `08c1152`)
- Added `"postgres: requires a running PostgreSQL service (skipped on SQLite-only jobs)"` to the `markers` array in `[tool.pytest.ini_options]` in `pyproject.toml`.
- Diff confined to the markers list; `slow` marker untouched.

### Task 2: CI workflow rewiring (commit `0182b1a`)
- **TEST-12:** Both `push` and `pull_request` triggers flipped from `branches: [ master ]` to `branches: [ main ]` (lines 5 and 7 of `.github/workflows/test.yml`).
- **TEST-11 rail:** Added new step `Run @pytest.mark.postgres suite` in the matrix `test` job (after the existing PG-specific test step). The step invokes `uv run pytest -m postgres -v --tb=short` against the existing `postgres:15` service container and tolerates pytest exit code 5 via `bash -c '...; rc=$?; [ $rc -eq 0 ] || [ $rc -eq 5 ]'`. A comment documents that the exit-code-5 tolerance is removed once Plan 03-07 tags tests.
- **SQLite deselect:** Appended `-m "not postgres"` to three pytest invocations:
  - Unit tests step (line 55)
  - Chaos/property tests step (line 61)
  - Coverage step (line 84)
- Existing `Run tests with PostgreSQL` step (atomic-claiming/scheduler tests) left untouched — it already runs against PG.
- `standalone-no-django` job unchanged; it has no pytest invocation.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocker] Verification fallback to plain-text grep**
- **Found during:** Task 1 verify step.
- **Issue:** `uv run pytest --markers` failed with "Failed to spawn: pytest" — the worktree venv has no pytest installed (cold checkout from base commit), and creating one would exceed the scope of this plan.
- **Fix:** Used a plain-text grep on `pyproject.toml` (`grep -q 'postgres: requires a running PostgreSQL'`) to confirm marker registration, consistent with the plan-checker W5 directive for Task 2. Functional verification of `pytest --markers` will occur naturally in CI on the next PR.
- **Files modified:** None (verification only).
- **Commit:** N/A.

No other deviations.

## Acceptance Criteria

- [x] `pyproject.toml` lists `postgres` in the markers array.
- [x] No `master` token remains under workflow triggers (`grep` confirms).
- [x] Both push and pull_request triggers on `main`.
- [x] CI job step invokes `pytest -m postgres` against the PG service container.
- [x] All SQLite-mode pytest invocations include `-m "not postgres"`.
- [x] Diff is purely additive + branch rename; no action versions bumped; no other jobs refactored.

## Known Stubs

None. The `postgres` CI step intentionally collects zero tests until Plan 03-07 tags them; this is documented in the workflow and tolerated via exit code 5. Tracked as plan dependency, not a stub.

## Threat Flags

None — diff confined to CI configuration and pytest marker registry; no new network/auth/data surface introduced.

## Commits

- `08c1152` — `chore(03-02): register postgres pytest marker`
- `0182b1a` — `ci(03-02): trigger on main and add postgres marker rail (TEST-12)`

## Self-Check: PASSED

- `pyproject.toml` contains `postgres: requires a running PostgreSQL` — FOUND.
- `.github/workflows/test.yml` contains `branches: [ main ]` x2 — FOUND.
- `.github/workflows/test.yml` contains `pytest -m postgres` — FOUND.
- `.github/workflows/test.yml` contains `-m "not postgres"` x3 — FOUND.
- `.github/workflows/test.yml` contains no `master` token — CONFIRMED.
- Commit `08c1152` in git log — FOUND.
- Commit `0182b1a` in git log — FOUND.
