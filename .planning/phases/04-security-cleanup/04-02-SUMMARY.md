---
phase: 04-security-cleanup
plan: 02
subsystem: security
tags: [security, allowlist, opt-in, SEC-04]
requires:
  - core/worker.py JobExecutor._import_task dispatch path
  - compat get_config() unified accessor
provides:
  - sqlery.core.security.check_task_module_allowed
  - sqlery.core.security.TaskModuleNotAllowed
  - sqlery.core.security.is_production_env
  - sqlery.core.security.warn_if_unconfigured
affects:
  - src/sqlery/core/worker.py
  - src/sqlery/django_sqlery/settings.py
  - src/sqlery/fastapi_sqlery/config.py
tech-stack:
  added: []
  patterns: [opt-in-allowlist, prefix-with-dot-boundary, BC-default-passthrough]
key-files:
  created:
    - src/sqlery/core/security.py
    - tests/unit/test_security.py
  modified:
    - src/sqlery/core/worker.py
    - src/sqlery/django_sqlery/settings.py
    - src/sqlery/fastapi_sqlery/config.py
decisions:
  - "Empty list config = unset semantics (allow all). Avoids ambiguity for operators who clear the list."
  - "Dot-boundary prefix match defends against myapp_evil bypass when myapp is on the list."
  - "Warning pinned to first line of WorkerProcess.run, before fork loop — guarantees once-per-run."
metrics:
  duration: ~12min
  completed: 2026-05-15
---

# Phase 04 Plan 02: ALLOWED_TASK_MODULES Opt-in Allowlist Summary

SEC-04 lands an opt-in `ALLOWED_TASK_MODULES` allowlist that restricts which Python module paths a worker may import when dispatching a job, with backward-compatible defaults (unset = allow all) and a production-env warning to nudge operators.

## What Was Built

**Task 1 — `src/sqlery/core/security.py` (107 lines, new):**
- `TaskModuleNotAllowed(Exception)` — raised at dispatch when a module is rejected.
- `check_task_module_allowed(module_path, allowed) -> None` — pass-through on None/empty; prefix-with-dot-boundary match otherwise.
- `is_production_env(env=None) -> bool` — case-insensitive substring `prod` scan across `ENV`, `ENVIRONMENT`, `DJANGO_SETTINGS_MODULE`.
- `warn_if_unconfigured(allowed)` — emits exactly one WARNING when production-shaped + unset.

**Task 2 — wiring across worker + both configs:**
- `JobExecutor._import_task` calls `check_task_module_allowed` BEFORE `importlib.import_module` runs (gate-before-import verified by test that asserts `importlib.import_module` is never reached when the allowlist rejects).
- `WorkerProcess.run` first executable line is `warn_if_unconfigured(get_config("ALLOWED_TASK_MODULES", None))` — pinned BEFORE the fork loop (W3). Static + behavioral tests confirm exactly one warning per `run()`, never per forked child.
- Django `DEFAULTS` gains `"ALLOWED_TASK_MODULES": None`.
- `StandaloneConfig._load_from_env` parses `SQLERY_ALLOWED_TASK_MODULES` as comma-separated, strips whitespace, drops empties, keeps None on all-empty/absent.

## Commits

| Hash    | Message |
|---------|---------|
| b9f5b76 | test(04-02): add failing tests for ALLOWED_TASK_MODULES allowlist (SEC-04) |
| eb19cee | feat(04-02): add ALLOWED_TASK_MODULES allowlist primitive (SEC-04) |
| 3903e6b | feat(04-02): wire ALLOWED_TASK_MODULES into worker dispatch + configs |

## Test Results

- `tests/unit/test_security.py` — 31 passed (primitive: 17, wiring: 4, dispatch gate: 4, W3 once-per-run: 2, smoke: 2, error-message: 1, plus 1 already counted).
- Wider unit suite: `288 passed, 9 skipped, 3 xfailed` (no regression).

## Deviations from Plan

**None.** Plan executed exactly as written. The only judgment call was choosing to extract `module_path` from `task_path` via `rsplit(".", 1)[0]` inside `_import_task` (the task path is `module.func`, not bare module); this matches the existing `import_task` semantics in `src/sqlery/core/utils.py`.

## Deferred / Out of Scope

- `tests/unit/test_worker.py::TestForkLifecycle::test_parent_branch_records_child_pid_and_waits` fails when run as part of the full unit sweep but passes in isolation. **Confirmed pre-existing** (reproduces on `git stash` baseline). Out of scope for SEC-04; not regressed by this plan.

## Self-Check: PASSED

- `[ -f src/sqlery/core/security.py ]` FOUND
- `[ -f tests/unit/test_security.py ]` FOUND
- `[ -f .planning/phases/04-security-cleanup/04-02-SUMMARY.md ]` FOUND
- Commits b9f5b76, eb19cee, 3903e6b all present in `git log`.
- `pyproject.toml` untouched.
- `STATE.md`, `ROADMAP.md` untouched.
