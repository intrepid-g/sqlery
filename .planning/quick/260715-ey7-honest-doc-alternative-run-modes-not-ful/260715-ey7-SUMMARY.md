---
status: complete
phase: 260715-ey7
plan: 01
subsystem: docs
tags: [documentation, run-modes, test-coverage, honesty-audit]
requires: []
provides:
  - docs/internal/RUN_MODES_STATUS.md
affects: []
tech-stack:
  added: []
  patterns: []
key-files:
  created:
    - docs/internal/RUN_MODES_STATUS.md
  modified:
    - .planning/STATE.md
key-decisions:
  - "Verdict vocabulary fixed to 5 honest levels; 'tested' requires actual CI execution per .github/workflows/test.yml"
  - "docs/internal/ is gitignored by repo convention (all 13 pre-existing internal docs untracked) — deliverable left untracked, NOT force-added"
duration: ~15min
completed: 2026-07-15
---

# Quick Task 260715-ey7: Honest Run-Modes Status Doc Summary

**One-liner:** Evidence-audited docs/internal/RUN_MODES_STATUS.md (302 lines) mapping all 9 run modes to implementation files, test files, and actual CI rails — exposing daemon_middleware/EventBridge as NO TESTS FOUND, django_tasks as CI-skipped, async as SQLite-only, and ENABLE_DAEMON as a config knob no code reads.

## What Was Done

### Task 1: Audit run-mode evidence and write RUN_MODES_STATUS.md
Audited every mode by reading implementation and test sources (not trusting the plan's list):
- Verified all 40 plan-cited paths exist; read CI workflow to classify strict vs tolerant rails (chaos and legacy-PG steps use `|| echo` — failures don't gate).
- Key honest findings baked into the doc:
  - `daemon_middleware` (DaemonMiddleware): NO TESTS FOUND (grep of tests/ empty).
  - `eventbridge_trigger`: NO TESTS FOUND; boto3 not even installable in CI (`[dev]` lacks it).
  - `django_tasks`: contrary to the plan's guess, tests DO reference it (tests/test_triggers.py, tests/test_subprocess.py) — but the real-path tests are `skipif(not HAS_DJANGO_TASKS)` and CI installs `.[dev]` without django-tasks, so only fallback paths run in CI.
  - Async worker: full unit+E2E suite runs in CI but with ZERO `postgres` marks — SQLite-only.
  - `ENABLE_DAEMON`: set by initialize()/config in 3 files, read by none — partial/aspirational.
  - Django subprocess "E2E" cell runs an in-process JobExecutor equivalent (per harness docstring); only the standalone cell spawns a real subprocess.
  - Lambda smoke tests are genuine E2E (no boto3 mocking) but SQLite-only; docs/ARCHITECTURE.md's "not production-ready" warning confirmed accurate.
- Task 1 automated verify passed: frontmatter keys present, all 6 required mode strings present, 302 lines (min 120).

### Task 2: Honesty verification pass
- Citation validity: all cited src/tests/.github paths exist on disk (automated check: "ALL CITED PATHS EXIST").
- Coverage claims: spot-checked that each cited test file imports the mode's module (test_middleware→sqlery.middleware, test_subprocess→sqlery.subprocess_executor, test_django_async_backend→django_sqlery.async_backend, etc.). No downgrades needed — doc was written from verified evidence.
- CI claims: all "runs in CI" statements match actual pytest invocations in .github/workflows/test.yml, including the tolerant-rail qualifiers.
- Language sweep: "production-ready" appears only negated ("NOT production-ready"); no "fully tested"/"battle-tested"/"complete coverage".

## Deviations from Plan

### 1. [Blocking] docs/internal/ is gitignored — Task commits skipped
- **Found during:** Task 1 commit step
- **Issue:** `.gitignore:73` ignores `docs/internal/`; every pre-existing internal doc (ISSUE_TRACKER.md, REGRESSIONS.md, etc.) is untracked — this is deliberate repo convention.
- **Resolution:** Deliverable created at the required path but NOT force-added (`git add -f` on gitignored content is prohibited). Per-task commits for Tasks 1–2 are therefore intentional no-ops; only the .planning/ metadata commit lands.
- **Files affected:** docs/internal/RUN_MODES_STATUS.md (untracked by design)

### 2. [Correction] Plan's grep hypothesis for django_tasks was wrong
- **Found during:** Task 1 audit
- **Issue:** Plan predicted `grep -rln django_tasks tests/` would be empty → verdict "NO TESTS FOUND". Grep actually hits tests/test_triggers.py and tests/test_subprocess.py.
- **Fix:** Verdict corrected to "fallback paths CI-tested; real django-tasks path tests exist but skipped in CI (package absent from [dev])".

## Self-Check: PASSED

- docs/internal/RUN_MODES_STATUS.md exists: FOUND
- Task 1 automated verify: PASSED (frontmatter keys, 6 mode strings, 302 lines)
- Task 2 automated verify: PASSED (ALL CITED PATHS EXIST)
- Spot-check NO TESTS FOUND claims: `grep -rln "daemon_middleware\|DaemonMiddleware" tests/` → empty; `grep -rln eventbridge tests/` → empty
