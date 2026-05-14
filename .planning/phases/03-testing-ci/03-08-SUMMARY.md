---
phase: 03-testing-ci
plan: 08
subsystem: testing/ci
tags: [coverage, ci, gate, followup]
requires: [03-03, 03-04, 03-05, 03-06, 03-07]
provides:
  - coverage-gate
  - coverage-html-artifact
affects:
  - pyproject.toml
  - .github/workflows/test.yml
tech-stack:
  added: []
  patterns: [pytest-cov fail_under, GitHub Actions artifact upload]
key-files:
  created:
    - .planning/phases/03-testing-ci/03-08-SUMMARY.md
  modified:
    - pyproject.toml
    - .github/workflows/test.yml
decisions:
  - "Set fail_under = 13 (baseline - 2) with [FOLLOWUP] tag instead of 70, per plan escalation rule for baseline < 65%"
  - "Did NOT silently lower threshold; documented baseline and reason in code comment and this summary"
  - "Did NOT attempt to fix the underlying test-infra issue (out of scope per CLAUDE.md SCOPE BOUNDARY); deferred to follow-up"
metrics:
  duration: ~10 min
  completed: 2026-05-14
---

# Phase 03 Plan 08: Coverage Gate Summary

**One-liner:** Project-wide coverage gate wired via `[tool.coverage.report] fail_under` + CI `--cov-fail-under` flag with HTML artifact upload; threshold set to baseline-2 = 13% with `[FOLLOWUP]` to raise toward 70%, due to pre-existing test-infra errors blocking accurate baseline measurement.

## Outcome

Three plan tasks executed:

1. **Task 1 (baseline measurement):** Project-wide coverage measured at **15%** (no-chaos run; 9764 statements, 8306 missed). Chaos run failed to collect (`tests/chaos/test_property_based.py` import error — out of scope). Per the plan's decision rule, **15% < 65% triggers STOP-and-surface**.

2. **Task 2 (config + CI):** Added `[tool.coverage.run]` and `[tool.coverage.report]` tables to `pyproject.toml` with the exact `omit` list from the plan (migrations, templates, stubs, `*_compat.py`, `lambda_handler.py`). Threshold set to `fail_under = 13` (baseline − 2) with an inline `[FOLLOWUP]` comment pointing to this summary. Updated `.github/workflows/test.yml` SQLite job to run `pytest ... --cov-fail-under=13 --cov-report=html` and to upload `htmlcov/` via `actions/upload-artifact@v4` with `retention-days: 14`. Commit: `a7649f5`.

3. **Task 3 (synthetic-fail verification):** Verified the gate mechanism by overriding the threshold to 70 via CLI flag (no source change needed — the `--cov-fail-under` CLI flag overrides `pyproject.toml`):
   - With `--cov-fail-under=70`: pytest exit **1**, message `FAIL Required test coverage of 70% not reached. Total coverage: 14.11%` — gate fires as expected.
   - With `--cov-fail-under=13`: message `Required test coverage of 13% reached. Total coverage: 14.11%` — gate passes (pytest still exits 1 because of the 196 test-collection errors, which is a separate concern; coverage gate semantics are correct).
   - No file revert needed (synthetic test was CLI-only).

## Deviations from Plan

### [Rule 2 — Escalation triggered] Baseline far below the 65% floor

- **Found during:** Task 1.
- **Issue:** Baseline coverage measured at **15%**, well below the plan's `< 65%` STOP threshold. Investigation revealed **196 test-collection errors** of the form `OperationalError: table "sqlery_daemon_lease" already exists` — a Django test-fixture issue where tables are created twice across the suite. This is a pre-existing test-infrastructure problem, not a coverage deficit in the production code.
- **Decision (per executor-mode parallel_execution rules):** Set `fail_under = baseline - 2 = 13` with an explicit `[FOLLOWUP]` tag in the pyproject comment. This honors the plan's "gate fires on regression but does not block CI right now" instruction. Did NOT silently set 70; did NOT delete files; did NOT modify test fixtures (CLAUDE.md SCOPE BOUNDARY).
- **Files modified:** `pyproject.toml` (FOLLOWUP comment), `.github/workflows/test.yml` (`--cov-fail-under=13`).
- **Commit:** `a7649f5`.

### Chaos-run baseline could not be captured

- **Issue:** `uv run pytest tests/` including chaos failed at collection (`tests/chaos/test_property_based.py` import error before any tests ran).
- **Decision:** Recorded the no-chaos baseline (15%) only. The chaos suite contributes a small portion of project coverage; its omission does not change the escalation branch.

## Key Decisions

1. **Honor the escalation rule over the happy path** — the plan explicitly says "do not silently lower the threshold" and the parallel_execution prompt confirms the baseline-2 with FOLLOWUP pattern for < 65% baselines. Did not invent a workaround.
2. **Did not touch STATE.md or ROADMAP.md** per orchestrator instruction.
3. **Did not add new dependencies** (CLAUDE.md constraint). `pytest-cov` already present.
4. **Belt-and-suspenders threshold** — `fail_under` lives in BOTH `pyproject.toml` AND the CI `--cov-fail-under` CLI flag, per the plan.

## Follow-Up Required (raise gate toward 70%)

Two follow-up workstreams blocked by the same root cause:

1. **Fix the 196 test-collection errors** — the `sqlery_daemon_lease`-already-exists pattern points to a fixture that creates tables outside the Django test-db lifecycle (likely a session-scoped fixture re-running migrations on a pytest-django in-memory DB that already has the tables). Once fixed, expect coverage to jump significantly (likely past 70%) because most test modules are currently aborting at setup, not running their bodies.
2. **Re-measure baseline + raise `fail_under` toward 70%** in a small follow-up plan (e.g. `04-XX-coverage-raise-threshold`). When raising, also fix `tests/chaos/test_property_based.py` collection error so chaos contributes to the measurement.

Plan-checker fix W3 (excluding `core/daemon.py` / `core/worker.py`) is **not needed at this time** because the gate is set well below their contribution; revisit after the test-infra fix.

## Threat Flags

None. No new network endpoints, auth paths, or trust-boundary changes introduced — this is config-only.

## Self-Check: PASSED

- `pyproject.toml` contains `fail_under = 13` and `source = ["src/sqlery"]` — verified via `tomllib`.
- `.github/workflows/test.yml` contains `cov-fail-under=13` and `upload-artifact@v4` — verified via grep.
- Commit `a7649f5` exists on `worktree-agent-a1ae858cae5ad37d4` — verified via `git log`.
- Synthetic-fail run with `--cov-fail-under=70` exited 1 with `FAIL Required test coverage of 70% not reached` — verified.
- No regression to existing `pyproject.toml` dependency declarations — `[project]` table untouched.
- STATE.md / ROADMAP.md not modified — verified by `git diff --stat HEAD~1..HEAD`.
