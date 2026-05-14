# Phase 03 — Plan Checker Fixes

Applied 2026-05-14 in response to plan-checker review of Phase 03 plans.

## Fixes Applied

| ID | Severity | Plan | What Changed |
|----|----------|------|--------------|
| B1 | BLOCKER | 03-04-PLAN.md (Task 1) | Added preamble: create `tests/unit/__init__.py` if missing; do NOT import from `tests/unit/conftest.py` (owned by Plan 03-03; may not exist when 03-04 runs in parallel). Task 1 is now self-contained w.r.t. fixtures. |
| B2 | BLOCKER | 03-07-PLAN.md (Task 2) | Replaced vague "if parametrization is already in place" branch with explicit Step A (grep for existing `db` axis) + Branch 1 (axis present → add `pytest.param('postgres', marks=[pytest.mark.postgres])` to existing list) + Branch 2 (no axis → add `@pytest.mark.parametrize('db', [...], indirect=True)` and route through `_build_harness(mode, integration, db)`). Acceptance: executor records the chosen branch in 03-07-SUMMARY.md. |
| W1 | WARN | 03-07-PLAN.md (frontmatter) | Added `TEST-01, TEST-02` to `requirements:` list. Plan 03-07 delivers the Postgres variants of the 6×2 mode-integration matrix (SQLite variants ship in 03-03..06). Objective + success criteria updated to reflect this. |
| W2 | WARN | 03-06-PLAN.md (Task 1) | Added explicit `enqueue(db_url, task_path, **kwargs)` helper alongside `spawn_worker`. Helper opens a short-lived SQLAlchemyBackend against `db_url` and calls the standard enqueue path. Task 2 test bodies now route job injection through this helper into the same SQLite file the worker subprocesses read. Added `key_link` and acceptance criterion. |
| W4 | WARN | 03-05-PLAN.md (Task 1) | Locked mocking to stdlib `unittest.mock`. Removed pytest-mock branching ("Use `mocker = pytest-mock` IF the project already depends on it"). Now states: "Use `unittest.mock.patch` (or `MagicMock`) directly — `pytest-mock` is intentionally not a dep." Added acceptance criterion: no `pytest-mock`/`pytest_mock` imports in the file. |
| W5 | WARN | 03-02-PLAN.md (Task 2 verify) | Replaced fragile PyYAML-parse trick (`d.get(True, d.get('on', {}))` — fails because YAML parses bare `on:` as `True`) with plain `grep -E "^on:\|branches:.*main"` plus negative grep `! grep -E "branches:.*master"` to assert no `master` token remains under triggers. |
| W3 | WARN (informational) | 03-03-PLAN.md (objective) | Added scope note acknowledging FakeBackend is the largest single deliverable (full DatabaseBackend ABC, 30+ abstract methods, expected 300-500 LOC). Added directive: if executor finds the plan exceeds ~10 tasks of work mid-flight, log the deviation in 03-03-SUMMARY.md rather than splitting. Task 1 action expanded to call out the 300-500 LOC expectation. |

All plan-checker fixes applied. Phase 03 plans ready for /gsd-execute-phase.
