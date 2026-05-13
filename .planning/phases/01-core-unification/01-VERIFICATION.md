---
phase: 01-core-unification
verified: 2026-05-13T00:00:00Z
status: human_needed
score: 6/6 must-haves verified (1 deferred human checkpoint)
overrides_applied: 0
human_verification:
  - test: "Push branch + open PR; confirm `standalone-no-django` CI job goes green with the lines `django absent OK` and `all-core-imports-ok` present in logs."
    expected: "GitHub Actions run for the standalone-no-django job exits 0 and the two sentinel strings appear in the step logs."
    why_human: "Plan 01-03 Task 3 is an explicit `checkpoint:human-verify` — the workflow change cannot self-verify from within a worktree branch. The verifier ran the equivalent local simulation and the pytest layer, but the GitHub-hosted CI run is the contracted UNIF-04/05/06 acceptance signal."
---

# Phase 1: Core Unification Verification Report

**Phase Goal:** Make `src/sqlery/core/` truly framework-agnostic — single source of truth for the claiming algorithm and the job executor, with zero Django imports in core modules. The package must be importable in a virtualenv without Django installed.

**Verified:** 2026-05-13
**Status:** human_needed (all code-level criteria PASS; one human CI checkpoint pending)
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| #   | Truth                                                                                          | Status     | Evidence |
| --- | ---------------------------------------------------------------------------------------------- | ---------- | -------- |
| 1   | `import sqlery.core` succeeds without Django installed (UNIF-04/05/06)                         | VERIFIED   | Verifier ran `PYTHONPATH=src python3 -c "..."` with a `MetaPathFinder` blocking `django` and `django.*`; all 12 core submodules imported, printed `ALL OK`. |
| 2   | No unguarded top-level `from django` / `import django` in `src/sqlery/core/*.py` (UNIF-03)     | VERIFIED   | `grep -rnE '^(from\|import) django' src/sqlery/core/*.py` returns zero matches (exit 1). |
| 3   | Duplicate claiming source retired; `django_sqlery/worker_claiming.py` is a delegation stub (UNIF-01) | VERIFIED | File is now 26 lines, header `# DEPRECATED 2026-05-13 — moved to sqlery.core.claiming. Remove after 2027-05-13.`, body re-exports from `sqlery.core.claiming` with a `__getattr__` fallback. |
| 4   | Duplicate executor source retired; `django_sqlery/executor.py` is a delegation stub (UNIF-02)  | VERIFIED   | File is now 42 lines, dated-stub header present, re-exports from `sqlery.core.worker` + `sqlery.django_sqlery._executor_impl`. Historic Django `TaskExecutor` body relocated (not duplicated) into the new `_executor_impl.py` (692 lines, single source of truth for that class). |
| 5   | Two-layer regression protection in place (pytest + CI)                                         | VERIFIED   | `tests/test_core_standalone.py` exists (105 lines, 2 tests, both PASSED locally: `2 passed in 0.76s`). `.github/workflows/test.yml` contains a `standalone-no-django` job (line 74) installing `.[standalone]`, asserting `django` is NOT installed (T-01-04 mitigation), then importing all 12 core submodules and printing `all-core-imports-ok`. |
| 6   | All in-repo callers route through canonical `sqlery.core.{claiming,worker}` (not the stubs)    | VERIFIED   | `grep` for `from .django_sqlery.{worker_claiming,executor} import` and `from sqlery.executor import` outside the stub files themselves finds only: (a) two source-controlled COMMENTS in `worker_pool.py:18` and `compat/scheduler.py:316` documenting the move, (b) one `__getattr__` lazy fallback inside `core/worker.py:793` (the documented design — see WARNING below), and (c) pre-existing legacy callers under `src/sqlery/management/commands/` and `tests/` that import from the top-level `sqlery.executor` stub. The stub is functional and dated; these are NOT regressions and the stubs intentionally exist for this scenario. |

**Score:** 6/6 truths verified.

### Required Artifacts

| Artifact                                            | Expected                                              | Status   | Details |
| --------------------------------------------------- | ----------------------------------------------------- | -------- | ------- |
| `src/sqlery/core/db_resilience.py`                  | Guarded `django.db` + `sqlalchemy.exc` imports; `_RETRYABLE_EXC` tuple; `_get_setting` via `get_config` | VERIFIED | Imports cleanly under Django-blocked subprocess; pytest `test_db_resilience_retry_works_without_django` PASSED. |
| `src/sqlery/core/model_utils.py`                    | Explicit `RuntimeError` when Django models unavailable | VERIFIED | Module imports without Django; runtime guard noted in 01-01 SUMMARY. |
| `src/sqlery/core/daemon_runner.py`, `worker_runner.py` | Guarded `import django; django.setup()`            | VERIFIED | Both modules import in Django-blocked subprocess. |
| `src/sqlery/core/log_config.py`                     | Already guarded (no change required)                  | VERIFIED | Imports cleanly without Django. |
| `src/sqlery/django_sqlery/worker_claiming.py`       | Dated deprecation stub (DEPRECATED 2026-05-13)        | VERIFIED | 26 lines; header + body match dead-code policy. |
| `src/sqlery/django_sqlery/executor.py`              | Dated deprecation stub (DEPRECATED 2026-05-13)        | VERIFIED | 42 lines; dated header + dual re-export from core + _executor_impl. |
| `src/sqlery/django_sqlery/_executor_impl.py`        | New internal home for historic Django `TaskExecutor`  | VERIFIED | 692 lines; only place the Django `TaskExecutor` class body lives. |
| `src/sqlery/executor.py`                            | Top-level dated deprecation stub                      | VERIFIED | 25 lines; dated header matches policy. |
| `tests/test_core_standalone.py`                     | Subprocess + MetaPathFinder-based regression test     | VERIFIED | 105 lines, 2 tests, both PASSED locally. |
| `.github/workflows/test.yml::standalone-no-django`  | New CI job, 3.10, no Django, asserts 12 core submodule imports | VERIFIED | Job present at line 74; contains T-01-04 mitigation, `[standalone]` extra install, sentinel `all-core-imports-ok`. |

### Key Link Verification

| From                                            | To                                | Via                                   | Status | Details |
| ----------------------------------------------- | --------------------------------- | ------------------------------------- | ------ | ------- |
| `sqlery.django_sqlery.worker_claiming.*`        | `sqlery.core.claiming.*`          | `from sqlery.core.claiming import *` + `__getattr__` proxy | WIRED  | Identity check noted in 01-02 SUMMARY (`is` returns True for `claim_next_job_with_queue_priority`, `release_job`, `get_node_id`). |
| `sqlery.django_sqlery.executor.TaskExecutor`    | `sqlery.django_sqlery._executor_impl.TaskExecutor` | Direct re-export             | WIRED  | Sole source of truth. |
| `sqlery.core.worker.TaskExecutor` (lazy)        | `sqlery.django_sqlery.executor.TaskExecutor` (Django mode) / `JobExecutor` (standalone) | Module-level `__getattr__` (core/worker.py:780–797) | WIRED | Falls back to `JobExecutor` on `ImportError` — design preserves UNIF-04 while keeping the public name. |
| `tests/test_core_standalone.py`                 | All 12 core submodules            | subprocess + MetaPathFinder           | WIRED  | Both tests PASS (`2 passed in 0.76s`). |
| `.github/workflows/test.yml::standalone-no-django` | 12 core submodule imports      | uv venv + `.[standalone]` extra       | WIRED  | YAML structurally valid; locally simulated by 01-03 plan author with sentinel strings printed. |

### Behavioral Spot-Checks

| Behavior                                                            | Command                                              | Result            | Status |
| ------------------------------------------------------------------- | ---------------------------------------------------- | ----------------- | ------ |
| `sqlery.core.*` imports without Django                              | `PYTHONPATH=src python3 -c "<meta-path-finder>; import sqlery.core.*"` | `ALL OK` | PASS   |
| Pytest standalone regression suite                                  | `PYTHONPATH=. uv run pytest tests/test_core_standalone.py -v`         | `2 passed in 0.76s` | PASS |
| No top-level Django imports in core                                 | `grep -rnE '^(from\|import) django' src/sqlery/core/*.py`             | (no output, exit 1) | PASS |
| No debt markers (TBD/FIXME/XXX) in Phase-1-modified files           | `grep -nE 'TBD\|FIXME\|XXX' <files>`                  | (no output)        | PASS  |

### Probe Execution

Not applicable — phase declares pytest + CI as its verification surface, not shell probes. Both surfaces exercised above.

### Requirements Coverage

| Requirement | Source Plan(s) | Description                                                                       | Status     | Evidence |
| ----------- | -------------- | --------------------------------------------------------------------------------- | ---------- | -------- |
| UNIF-01     | 01-02          | Claiming consolidated — `django_sqlery/worker_claiming.py` delegates to `core/claiming.py` | SATISFIED  | Truth #3 / Stub at 26 lines. |
| UNIF-02     | 01-02          | Execution consolidated — `django_sqlery/executor.py` delegates to `core/worker.py` | SATISFIED  | Truth #4 / Stub at 42 lines + `_executor_impl.py` relocation. |
| UNIF-03     | 01-01          | Django imports removed from `core/`                                                | SATISFIED  | Truth #2 / `grep` returns zero matches. |
| UNIF-04     | 01-01, 01-03   | `core/worker.py` works without Django                                              | SATISFIED  | Truth #1 + pytest suite + CI job. |
| UNIF-05     | 01-01, 01-03   | `core/daemon.py` works without Django                                              | SATISFIED  | Truth #1 + pytest suite + CI job. |
| UNIF-06     | 01-01, 01-03   | `core/db_resilience.py` works without Django                                       | SATISFIED  | Truth #1 + pytest `test_db_resilience_retry_works_without_django` PASS. |

No ORPHANED requirements detected.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
| ---- | ---- | ------- | -------- | ------ |
| (none in Phase-1-modified files) | — | — | — | grep for TBD/FIXME/XXX returned empty. |

WARNING (informational, not a gap): Several pre-existing legacy callers still import from the top-level `sqlery.executor` stub:
- `src/sqlery/management/commands/run_jobs.py:9`
- `src/sqlery/management/commands/run_scheduled_tasks.py:4`
- `tests/test_atomic_claiming.py:25`, `tests/test_api.py:168,183`, `tests/test_queue.py:27`, `tests/test_executor.py:6`, `tests/test_atomic_scheduler.py:29`, `tests/test_concurrency_and_timeout.py:21`

These are exactly the audience the dated stubs exist to serve (the stubs delegate via re-export + `__getattr__`). They are NOT a regression and Phase 1 explicitly defers in-repo legacy-call cleanup ("Annotating the 21 top-level backward-compat stubs with removal dates" → Phase 4 per CONTEXT.md `<deferred>`). The 12 callers the plan promised to rewrite (admin.py, backend.py, daemon_worker.py, worker_process.py, worker_registry.py, three management commands under `django_sqlery/`, `triggers.py`, `lambda_handler.py`, `compat/scheduler.py`, `tests/chaos/test_worker_chaos.py`) were rewritten — verified absent from the grep.

### Human Verification Required

1. **CI green on PR for `standalone-no-django` job**
   - **Test:** Push the merged branch / open a PR; observe GitHub Actions.
   - **Expected:** The `standalone-no-django` job exits 0 and its step logs contain both `django absent OK` and `all-core-imports-ok`.
   - **Why human:** Plan 01-03 Task 3 is an explicit `checkpoint:human-verify` — the workflow change cannot self-verify from within a worktree branch. The pytest layer + local CI simulation gave the same signal, but contractually UNIF-04/05/06 acceptance is the live GitHub-hosted run.

### Gaps Summary

No code-level gaps. All six success criteria for Phase 1 are observable in the merged codebase:

1. Standalone import works (verified by direct subprocess execution).
2. No unguarded Django imports remain in `core/`.
3. Duplicate claiming source retired.
4. Duplicate executor source retired (with the historic Django class relocated, not duplicated).
5. Two-layer regression protection in place — pytest passing locally; CI workflow YAML present and structurally valid.
6. All 12 in-repo callers (the ones the plan committed to rewriting) route through canonical paths.

The lone outstanding item is the human-verify checkpoint from Plan 01-03 (CI run green on a PR). This was intentionally deferred per the plan's `type: checkpoint:human-verify` task definition and is not a defect.

**Recommendation:** Mark Phase 1 as code-complete and request the user/orchestrator to satisfy the human-verify CI checkpoint as the final acceptance gate. Once the CI run is observed green on a PR, status flips to `passed` with no further code changes required.

---

_Verified: 2026-05-13_
_Verifier: Claude (gsd-verifier)_
