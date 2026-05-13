---
phase: 02-execution-modes
plan: 01
subsystem: dependencies
tags: [django-version, ci, breaking-change]
requires: []
provides: [django-5.2-floor]
affects: [pyproject.toml, .github/workflows/test.yml, CHANGELOG.md, uv.lock]
tech-stack:
  added: []
  patterns: []
key-files:
  created: []
  modified:
    - pyproject.toml
    - uv.lock
    - .github/workflows/test.yml
    - CHANGELOG.md
decisions:
  - "Django 5.2 LTS adopted as new minimum (CONTEXT.md A.1 LOCKED, ASYN-02)"
  - "Dropped optional 5.1 smoke row to keep matrix lean"
metrics:
  duration: "~5min"
  completed: 2026-05-13
---

# Phase 2 Plan 1: Bump Django Minimum to 5.2 LTS — Summary

Raised Django dependency floor to 5.2 LTS across pyproject.toml, refreshed uv.lock, added 5.2 to CI test matrix, and announced BC break in CHANGELOG.

## What Changed

- **pyproject.toml**: 4 `django>=4.2` pins → `django>=5.2` (main optional-deps `django`, `all-django`, `all`, `dev`).
- **uv.lock**: Refreshed via `uv lock --upgrade-package django` (resolved Django 5.2.14 and 6.0.5 candidates).
- **.github/workflows/test.yml**: Added `django-version: ['5.2']` matrix axis and explicit `uv pip install "django==5.2.*"` step. No prior 4.2 entries existed.
- **CHANGELOG.md**: New "Breaking" section under `[0.11.0] - Unreleased` documenting the floor bump and its ASYN-02 rationale.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] CI workflow filename mismatch**
- **Found during:** Task 2.
- **Issue:** Plan specifies `.github/workflows/ci.yml` and `.github/workflows/postgres.yml`. Actual repo has only `.github/workflows/test.yml` (combines unit, chaos, and Postgres jobs).
- **Fix:** Applied matrix changes to `test.yml`. Postgres is already exercised in the same job via the `postgres:15` service container, so no separate workflow is needed.
- **Commit:** `4b801a9`

**2. [Rule 1 - Bug] Django version was not previously pinned in CI**
- **Found during:** Task 2.
- **Issue:** CI had no `django-version` matrix axis at all — Django was installed transitively via `[dev]`, meaning the new 5.2 floor in pyproject.toml would have been the only enforcement, with no smoke row exercising it.
- **Fix:** Added `django-version: ['5.2']` matrix axis plus explicit pinned install (`uv pip install "django==5.2.*"`) so the matrix is now a genuine guardrail.
- **Commit:** `4b801a9`

### Skipped

- **Optional 5.1 smoke row**: Plan marked it OPTIONAL "if low cost." Skipped to keep the matrix at the minimum viable surface; can be added later if regressions show on 5.1.

## Checkpoint Status

Plan declared a final `checkpoint:human-verify` (push PR, confirm CI runs 5.2 row, eyeball CHANGELOG wording). In the parallel worktree, the executor cannot pause for human verification; the orchestrator/user should perform this check after the wave merges. Suggested verifications:

1. Open draft PR; confirm CI matrix shows `(3.11|3.12|3.13) × 5.2` jobs and no 4.2 jobs.
2. Skim CHANGELOG "Breaking" wording.

## Verification

- `grep -nE 'django\s*>=\s*4\.' pyproject.toml | grep -v pytest-django` → empty.
- `grep -rE 'django[-_=]?version.*4\.2|django==4\.2' .github/workflows/` → no matches.
- `grep -q 'Django.*5\.2' CHANGELOG.md && grep -qi breaking CHANGELOG.md` → OK.
- `uv lock --upgrade-package django` resolved successfully against 5.2 floor.

## Commits

- `ac48df7` — chore(02-01): bump Django minimum to 5.2 LTS
- `4b801a9` — ci(02-01): add Django 5.2 matrix and pin install in CI
- `6904d85` — docs(02-01): announce Django 5.2 BC break in CHANGELOG

## Self-Check: PASSED

- pyproject.toml: FOUND (4× django>=5.2)
- uv.lock: FOUND (modified)
- .github/workflows/test.yml: FOUND (django-version matrix axis present)
- CHANGELOG.md: FOUND (Breaking section present)
- Commits ac48df7, 4b801a9, 6904d85: all FOUND in `git log`.
