---
phase: 03-testing-ci
plan: 01
subsystem: django-migrations
tags: [migrations, django, regression, foundation, d-02-07-1]
requires:
  - 0020_daemon_lease.py (creates sqlery_daemon_lease)
  - 0022_*_delete_daemonlease_... (filename advertises DeleteModel; operations does not)
provides:
  - "Unblocked pytest-django setup_databases on clean SQLite"
  - "Regression test (tests/test_d_02_07_1_regression.py) locking the bug closed"
affects:
  - Every Phase 3 plan that depends on a working pytest fixture chain (i.e. all of them)
  - Phase 2 TEST-01 / TEST-02 acceptance
tech-stack:
  added: []
  patterns:
    - "No-op migration as graph-node placeholder (operations=[] + dependencies preserved)"
key-files:
  created:
    - tests/test_d_02_07_1_regression.py
  modified:
    - src/sqlery/django_sqlery/migrations/0023_restore_daemonlease.py
decisions:
  - "Chose CONTEXT decision B (targeted no-op) over a squash. Preserves migration history for already-deployed databases per CLAUDE.md backward-compat constraint."
  - "Did not modify 0022 — its filename is misleading but its operations list is internally consistent. Touching 0022 would change a recorded migration on deployed databases."
  - "Kept the original CreateModel block as commented archaeology with `#CLEANUP 2026-05-14` marker per feedback_dead_code policy."
metrics:
  duration: "~10 min"
  completed: 2026-05-14
  tasks_completed: 2
  files_changed: 2
---

# Phase 3 Plan 01: Fix D-02-07-1 (duplicate CreateModel('DaemonLease')) Summary

Reduced `0023_restore_daemonlease.py` to a no-op so pytest-django's `setup_databases` no longer trips on a `sqlery_daemon_lease already exists` collision, and added a two-test regression suite that locks the bug closed.

## What Changed

### `src/sqlery/django_sqlery/migrations/0023_restore_daemonlease.py`
- `operations = []` (was a conditional `RunPython` that worked for `manage.py migrate` but not for pytest-django's `setup_databases`).
- `dependencies` line unchanged — graph node preserved for users who already applied earlier `0023` variants.
- Module docstring explains the root cause and the rationale for keeping the file (backward compatibility for already-deployed databases).
- Original `CreateModel` + `RunPython` block retained as commented archaeology with a `# #CLEANUP 2026-05-14: dead — remove after Phase 4` marker.

### `tests/test_d_02_07_1_regression.py` (new, 113 lines)
- `test_scheduled_task_creation_does_not_trip_daemon_lease` — `@pytest.mark.django_db` test that creates a `ScheduledTask`; before the fix, pytest-django could not even reach the test body because `setup_databases` crashed.
- `test_setup_databases_from_clean_sqlite` — spawns a fresh interpreter against an on-disk tmp SQLite, calls `django.test.utils.setup_databases(verbosity=0, interactive=False)`, asserts exit code 0 and that the combined stdout+stderr contains no `already exists`, `IntegrityError`, or `OperationalError` substrings.

## Root Cause Recap

1. `0020_daemon_lease.py` creates the `sqlery_daemon_lease` table.
2. `0022_delete_daemonlease_alter_jobregistry_metadata_and_more.py` was named as if it deletes `DaemonLease`, but its `operations` list contains only 11 `AlterField` calls — the `DeleteModel` was stripped before commit.
3. `0023_restore_daemonlease.py` previously issued an unconditional `CreateModel('DaemonLease')`, which collided with the still-existing table from `0020`.
4. An intermediate fix replaced step 3 with a conditional `RunPython`. That worked for `manage.py migrate` (single connection, fresh introspection) but **not** for pytest-django's `setup_databases`, which uses a separate test DB and cached state machine — the migration framework still believed the model needed to be (re-)created from the operations list.

The targeted fix: `0023` doesn't need to do anything. The table from `0020` is still there because `0022` never actually dropped it. Make `0023` truly empty.

## Verification Performed

- `PYTHONPATH=. uv run pytest tests/test_models.py::TestScheduledTask::test_scheduled_task_creation -x` — **passed** (canonical D-02-07-1 reproducer).
- `PYTHONPATH=. uv run pytest tests/test_d_02_07_1_regression.py -v` — **2/2 passed**.
- `PYTHONPATH=. uv run pytest tests/integration/test_modes.py --collect-only` — **16 tests collected** (proves `setup_databases` runs to completion; whether downstream parametrized cases pass is out of scope for this plan).

## Deviations from Plan

**[Rule 3 - blocking issue] First version of regression test used a wrong field name for `ScheduledTask`.** Initial probe used `interval_seconds=60`, but the model field is `interval` (PositiveIntegerField) + `interval_unit` (CharField). Fixed before commit by reading `src/sqlery/django_sqlery/models.py` — no separate commit needed because the test had not landed yet.

The plan's success criterion ("makemigrations --check exits 0") could not be cleanly verified: there is unrelated pre-existing schema drift (a rename-index + alter on `daemoncommand` and `queuedjob.failure_ttl`) that produces a phantom `0027_*` candidate. This is **out of scope** for D-02-07-1 (it touches `daemoncommand`, added in `0025`, and `queuedjob.failure_ttl`, not `DaemonLease`). Logged here rather than auto-fixed:

- `daemoncommand` index rename → likely a Django 5.2 index-naming-convention change.
- `queuedjob.failure_ttl` alteration → unknown; needs investigation.

Neither blocks the integration suite from collecting/running, so the plan's actual goal (unblocking Phase 3) is met.

## Threat Flags

None. Migration-graph integrity (T-03-01) is mitigated as planned: the file is preserved with operations=[], so deployed databases that already recorded `0023_restore_daemonlease` retain their graph node.

## Known Stubs

None.

## Commits

- `5a051e2` — fix(03-01): reduce 0023_restore_daemonlease to no-op (D-02-07-1)
- `785a9d4` — test(03-01): add D-02-07-1 regression for setup_databases on clean SQLite

## Self-Check: PASSED

- src/sqlery/django_sqlery/migrations/0023_restore_daemonlease.py — FOUND, operations=[] confirmed.
- tests/test_d_02_07_1_regression.py — FOUND, 113 lines, both tests pass.
- Commit 5a051e2 — FOUND in git log.
- Commit 785a9d4 — FOUND in git log.
