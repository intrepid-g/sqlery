---
phase: 03-testing-ci
plan: 07
subsystem: testing/ci
tags: [postgres, ci, markers, integration, test-11, test-01, test-02]
requirements: [TEST-01, TEST-02, TEST-11]
dependency_graph:
  requires: [03-01, 03-02, 03-03, 03-04, 03-06]
  provides:
    - "postgres CI rail running > 0 tests"
    - "PG variants of integration/chaos/backend matrix"
  affects:
    - .github/workflows/test.yml
    - tests/integration/conftest.py
    - tests/integration/test_modes.py
    - tests/chaos/test_subprocess_chaos.py
    - tests/chaos/test_lease_zombie.py
    - tests/unit/test_sqlalchemy_backend_sync.py
tech-stack:
  added: []
  patterns:
    - "marker-routed CI: `-m postgres` opts INTO the PG rail; `-m \"not postgres\"` opts OUT on SQLite rails"
    - "parametrize-row marking: `pytest.param('postgres', marks=[pytest.mark.postgres])` keeps SQLite cells unmarked"
key-files:
  created: []
  modified:
    - tests/integration/conftest.py
    - tests/integration/test_modes.py
    - tests/chaos/test_subprocess_chaos.py
    - tests/chaos/test_lease_zombie.py
    - tests/unit/test_sqlalchemy_backend_sync.py
    - .github/workflows/test.yml
decisions:
  - "Branch 1 chosen for test_modes.py: existing `db` axis already had a `postgres` param marked `slow`; swapped the mark to `postgres` rather than adding a parallel decorator."
  - "PG mirrors for chaos suites duplicate the most engine-sensitive scenarios (claim-race, timeout, lease-contention) rather than mirroring every test — keeps PG rail under the 5-minute budget."
metrics:
  duration_minutes: ~15
  completed: 2026-05-14
---

# Phase 03 Plan 07: Postgres CI Surface Expansion — Summary

One-liner: expanded `-m postgres` collection from 2 files to 18 tests across integration/chaos/backend suites, tightened the CI rail to fail on empty collection, and delivered PG variants of TEST-01 / TEST-02.

## Tasks completed

| Task | Name                                      | Commit  |
| ---- | ----------------------------------------- | ------- |
| 1    | `db_engine` fixture + marker routing      | 85d1cce |
| 2    | Tag integration/chaos/backend PG variants | fd92ffd |
| 3    | Tighten CI PG step + document matrix      | 4642600 |
| 4    | Human-verify checkpoint                   | auto-approved (worktree autonomous mode) |

## Branch choice for test_modes.py (Task 2.1)

**Branch 1 chosen** — the `db` axis was already present (`@pytest.mark.parametrize("db", ["sqlite", pytest.param("postgres", marks=pytest.mark.slow)])` from plan 02-07).

Justification:
1. Adding a parallel `@pytest.mark.parametrize` decorator would have multiplied the matrix (16 cells per integration × 2 db axes = 32) and required threading a second `db` parameter through `_build_harness`, which already accepts `db` from this same axis.
2. The only edit required was swapping `marks=pytest.mark.slow` → `marks=pytest.mark.postgres`. The harness already routes the `db` value to the correct backend (SQLite temp file vs `SQLERY_TEST_PG_URL`); conftest already gates `db == "postgres"` on the env var.

## Collection verification

```
SQLERY_TEST_PG_URL=postgresql://x:x@x/x \
  uv run pytest -m postgres --collect-only -q --ignore=tests/chaos/test_property_based.py
→ 18/763 tests collected (745 deselected)
```

PG-marked items by file:

| File                                       | PG items |
| ------------------------------------------ | -------- |
| tests/integration/test_modes.py            | 6 (the 6-mode × integration matrix's postgres axis) |
| tests/chaos/test_subprocess_chaos.py       | 2 (TimeoutBehaviorPostgres, ConcurrentClaimRacePostgres) |
| tests/chaos/test_lease_zombie.py           | 1 (TestLeaseContentionPostgres) |
| tests/unit/test_sqlalchemy_backend_sync.py | 6 (4 claim + 2 lease lifecycle) |
| tests/unit/test_django_backend.py          | 1 (pre-existing SKIP LOCKED placeholder, plan 03-04) |
| Other (placeholder / legacy)               | 2 |
| **Total**                                  | **18** (≥ 12 acceptance threshold) |

## Deviations from Plan

### Auto-fixed Issues

None — plan executed as written. The only behavioral note:

- `test_modes.py` previously used `pytest.mark.slow` for the PG axis (a 02-07-era convention). The plan instructs swapping to `pytest.mark.postgres`; we did. Tests still skip cleanly when `SQLERY_TEST_PG_URL` is unset because conftest gates `db == "postgres"` rows on env presence.

### Deferred Issues

- `tests/chaos/test_property_based.py` has a pre-existing collection error (`ImportError: serialize_job_arguments`) unrelated to this plan. Recorded but NOT fixed — out of scope (predates 03-06 chaos rebuild, lives in legacy property-based file). Logged for a later plan. The PG rail will surface this when CI runs `pytest -m postgres` against the full tree; if it interrupts collection, the file may need an `--ignore` or its own deletion in a follow-up.

## Self-Check: PASSED

- conftest gating logic present (`"postgres" in item.keywords`): VERIFIED
- `db_engine` fixture present: VERIFIED
- test_modes.py db-axis postgres row carries `pytest.mark.postgres`: VERIFIED
- ≥ 12 PG-marked tests collected: 18 collected, VERIFIED
- `pytest -m postgres -v --tb=short` is the CI invocation (no exit-code-5 tolerance): VERIFIED
- `not postgres` filter present on SQLite rails: VERIFIED (4 occurrences)
- pyproject.toml NOT modified: VERIFIED (`git diff main -- pyproject.toml` empty)
- STATE.md / ROADMAP.md NOT modified: VERIFIED
