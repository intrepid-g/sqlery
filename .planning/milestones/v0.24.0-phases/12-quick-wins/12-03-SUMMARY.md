---
phase: 12-quick-wins
plan: "03"
subsystem: packaging-ci
tags: [python-floor, ci-matrix, requires-python, classifiers]
dependency_graph:
  requires: []
  provides: [R11]
  affects: [pyproject.toml, .github/workflows/test.yml, .planning/PROJECT.md]
tech_stack:
  added: []
  patterns: [comment-out-not-delete]
key_files:
  created: []
  modified:
    - pyproject.toml
    - .github/workflows/test.yml
decisions:
  - "Python floor raised from 3.10 to 3.13 via requires-python and CI matrix update (user-approved 2026-06-10)"
  - "Old lines commented out (not deleted) per project convention"
  - "Task 3 (PROJECT.md) confirmed no-op — doc ingest already applied the 3.13 constraint"
metrics:
  duration_minutes: 2
  completed_date: "2026-06-10"
  tasks_completed: 3
  files_modified: 2
---

# Phase 12 Plan 03: Python 3.13 Floor Summary

Raised sqlery's Python floor from 3.10 to 3.13 by updating `requires-python` in pyproject.toml, pruning the 3.10/3.11/3.12 classifiers, and narrowing the CI matrix to 3.13 only — plus updating the standalone-no-django job from 3.10 to 3.13.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Bump requires-python and prune classifiers in pyproject.toml | 62a42fb | pyproject.toml |
| 2 | Update CI matrix and standalone-no-django job to Python 3.13 | a2f549c | .github/workflows/test.yml |
| 3 | Update PROJECT.md Constraints section | (no-op) | .planning/PROJECT.md |

## Verification Results

All four plan verifications passed:

1. `python3 -c "import tomllib; ..."` — `requires-python = ">=3.13"` confirmed via tomllib parse
2. `grep -v "^#..."` on test.yml — no active python-version lines reference 3.10/3.11/3.12
3. Two active python-version 3.13 entries in test.yml (`['3.13']` matrix + `'3.13'` standalone job)
4. `grep -v "<!--" .planning/PROJECT.md | grep "3.13"` — Constraints section actively states 3.13+

## Deviations from Plan

### Task 3: No-op (pre-applied by doc ingest)

**Found during:** Task 3 pre-read
**Issue:** The plan anticipated PROJECT.md might still have `3.10+` active. However, the doc ingest on 2026-06-10 had already applied the change — line 96 is `<!-- - **Python version**: 3.10+ minimum ... -->` (commented out) and line 97 is `- **Python version**: 3.13+ minimum (...)` (active).
**Fix:** No edits made. Confirmed as no-op and documented here per plan instructions.
**Files modified:** None

## Known Stubs

None — all changes are configuration/metadata updates with no UI or data-flow stubs.

## Threat Flags

None — this plan makes no network endpoints, auth paths, file access patterns, or schema changes.

## Self-Check

### Created files exist:
- `.planning/phases/12-quick-wins/12-03-SUMMARY.md` — this file

### Commits exist:
- 62a42fb — chore(12-03): raise requires-python to 3.13, comment out 3.10/3.11/3.12 classifiers
- a2f549c — chore(12-03): drop 3.11/3.12 from CI matrix, update standalone job to 3.13

## Self-Check: PASSED
