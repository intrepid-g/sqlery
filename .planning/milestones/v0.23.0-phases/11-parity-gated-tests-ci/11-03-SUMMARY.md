---
phase: 11-parity-gated-tests-ci
plan: 03
subsystem: ci
tags: [github-actions, ci, postgres, parity-matrix, standalone, gate]

# Dependency graph
requires:
  - phase: 11-parity-gated-tests-ci
    plan: 01
    provides: "Django x PG + standalone x PG cron parity cells (PARITY-02/03) in tests/test_atomic_scheduler.py and tests/test_core_standalone.py"
  - phase: 11-parity-gated-tests-ci
    plan: 02
    provides: "tests/test_parity_scheduler.py failover + bare-worker cells (PARITY-01/04); standalone PG cells"
provides:
  - "PARITY-05: all four parity matrix cells ACTUALLY RUN in CI and a failing/skipped cell fails the build"
  - "New test-job step 'Run standalone-mode parity suite with PostgreSQL' under SQLERY_FORCE_STANDALONE=1 + -m postgres, with empty-collection rejection"
affects: [parity-gated-tests-ci, ci-acceptance-gate]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Force standalone backend in CI even with Django installed via SQLERY_FORCE_STANDALONE=1 (matches tests/integration/conftest.py::_run_no_django)"
    - "--collect-only pre-check before the real run to turn pytest exit-5 (empty collection) into a job failure (no || echo / continue-on-error)"

key-files:
  created: []
  modified:
    - .github/workflows/test.yml

key-decisions:
  - "Standalone x Postgres cells run in the existing test job (postgres:15 service already attached); the standalone-no-django job stays import-only and is NOT the home of these cells"
  - "Scoped the new step to the three parity files to keep the forced-standalone PG run fast/focused; the matrix python-version fan-out gives full grid coverage without a new axis"
  - "Annotated (not modified) the existing PG rail to document it enforces the Django x Postgres parity cells — its no-path-filter -m postgres already collects them"

patterns-established:
  - "Two-cell-per-rail gate: default rail (-m 'not postgres') covers both SQLite cells; Django PG rail covers Django x PG; new forced-standalone step covers standalone x PG"

requirements-completed: [PARITY-05]

# Metrics
duration: ~1min
completed: 2026-06-08
---

# Phase 11 Plan 03: Standalone x Postgres Parity CI Gate Summary

**Closes the silently-skipped standalone x Postgres parity cell by adding a CI step that forces the standalone backend (SQLERY_FORCE_STANDALONE=1) and runs the parity files under -m postgres against postgres:15, with an empty-collection pre-check and no escape hatch — making PARITY-05 a first-class build gate where all four matrix cells actually execute.**

## Performance

- **Duration:** ~1 min
- **Started:** 2026-06-08T11:42:03Z
- **Completed:** 2026-06-08T11:42:56Z
- **Tasks:** 1
- **Files modified:** 1

## Accomplishments
- Added the `Run standalone-mode parity suite with PostgreSQL` step to the `test` job in `.github/workflows/test.yml`, immediately after the existing `Run @pytest.mark.postgres suite` step. Its env sets `PYTHONPATH: .`, `SQLERY_FORCE_STANDALONE: "1"`, and `SQLERY_TEST_PG_URL` + `DATABASE_URL` pointed at the `postgres:15` service (URLs copied verbatim from the existing PG step).
- The step runs a `--collect-only` pre-check over `tests/test_parity_scheduler.py tests/test_core_standalone.py tests/test_atomic_scheduler.py`, then the real `uv run pytest -m postgres -v --tb=short` over the same files. The pre-check turns pytest exit-code-5 (empty collection — i.e. a missing/skipped standalone PG cell) into a job failure. No `|| echo` tolerance and no `continue-on-error`, so a failing standalone PG parity cell fails the build.
- Annotated the existing `Run @pytest.mark.postgres suite` step with a `# Phase 11 (PARITY-05):` comment documenting that — having no path filter — it already collects the Django x Postgres parity cells (`tests/test_parity_scheduler.py` plus the PG cells in `tests/test_atomic_scheduler.py` / `tests/test_core_standalone.py`). Its run command was left unchanged.
- The matrix already fans out over python-version `['3.11','3.12','3.13']`, so the new standalone PG step runs on every Python version — full grid coverage without a new matrix axis.

## The Four-Cell Grid (now all enforced)

| Cell | Where it runs |
|------|---------------|
| Django x SQLite | Default rail: `Run unit tests` (`-m "not postgres"`) |
| Standalone x SQLite | Default rail: `Run unit tests` (`-m "not postgres"`) |
| Django x Postgres | Existing `Run @pytest.mark.postgres suite` (`-m postgres`, Django backend via pytest-django) |
| Standalone x Postgres | NEW `Run standalone-mode parity suite with PostgreSQL` (`SQLERY_FORCE_STANDALONE=1` + `-m postgres`) |

## Task Commits

1. **Task 1: Add standalone x Postgres parity CI step + confirm Django PG collection (PARITY-05)** - `2d462e0` (ci)

## Files Created/Modified
- `.github/workflows/test.yml` (modified, +32 lines, 0 deletions) - new standalone-mode PG parity step + `# Phase 11` annotations on the existing PG rail. Purely additive.

## Decisions Made
- Placed the standalone PG cells in the existing `test` job because it already attaches the `postgres:15` service; the `standalone-no-django` job remains import-only (it never runs pytest) and is unsuitable.
- Scoped the new step to the three parity files (rather than all `-m postgres` tests) to keep the forced-standalone PG run fast and focused on the parity matrix.
- Used a `--collect-only` pre-check to reject empty collections instead of relying on the run command's own exit code, since pytest treats "no tests collected" (exit 5) as non-fatal in some configurations.

## Deviations from Plan

None - plan executed exactly as written. The two changes were purely additive (new step + comments), so no `# Old:` commenting was required under CLAUDE.md edit discipline.

## Edit Discipline (CLAUDE.md)

`git diff .github/workflows/test.yml` shows **no removed non-comment lines** — only additions. The existing PG-rail run command and chaos/legacy `|| echo` lines were left untouched. The only `|| echo` occurrences remaining (lines 67, 74) are pre-existing on the chaos and legacy PostgreSQL steps; the new step has none.

## Verification

- YAML parses cleanly: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/test.yml'))"` exits 0; the parsed `test` job step list includes `Run standalone-mode parity suite with PostgreSQL`.
- `grep -n "Run standalone-mode parity suite with PostgreSQL"` → line 101.
- `grep -n "SQLERY_FORCE_STANDALONE"` → line 104 (env block of the new step) + comment.
- `grep -n "test_parity_scheduler.py"` → lines 109, 113 (collect pre-check + real run).
- `grep -n "Phase 11"` → lines 83, 91 (annotation on existing PG rail + lead-in to new step).
- New step run lines contain no `|| echo`.

## Known Stubs

None.

## Threat Flags

None — no new security surface. The step reuses the existing `postgres:15` service credentials (postgres/postgres); no new secret is introduced (matches threat register dispositions T-11-03-03 accept, T-11-03-SC accept). The mitigations for T-11-03-01 and T-11-03-02 (no skipped-but-counted cell, no advisory/non-failing step) are implemented via the `--collect-only` empty-collection rejection and the absence of any `|| echo` / `continue-on-error` escape.

## User Setup Required

None — the CI Postgres rail sets `SQLERY_TEST_PG_URL` automatically. No local action required.

## Self-Check: PASSED

- FOUND: .github/workflows/test.yml (modified)
- FOUND: .planning/phases/11-parity-gated-tests-ci/11-03-SUMMARY.md
- FOUND commit: 2d462e0 (Task 1)

---
*Phase: 11-parity-gated-tests-ci*
*Completed: 2026-06-08*
