---
phase: 04-security-cleanup
plan: 01
subsystem: cleanup
tags: [cleanup, dead-code, refactor, bugfix]
requires: []
provides:
  - sqlery.django_sqlery.webhooks (canonical module)
  - Phase 04 Wave-2 unblocked (no more import-error landmine in models.py)
affects:
  - src/sqlery/webhooks.py (BC stub via sys.modules aliasing)
  - 20 BC stubs at src/sqlery/*.py (date-stamped)
  - 4 commented-block hotspots (markers added)
  - src/sqlery/django_sqlery/worker_process.py (arity fix)
tech-stack:
  added: []
  patterns:
    - sys.modules identity-aliasing for BC stub (preserves patch.object semantics)
    - AST-based regression test for call-site arity
key-files:
  created:
    - src/sqlery/django_sqlery/webhooks.py
    - tests/unit/test_clean04_webhook_import.py
    - tests/unit/test_worker_process_arity.py
  modified:
    - src/sqlery/webhooks.py
    - src/sqlery/django_sqlery/worker_process.py
    - src/sqlery/{admin,apps,cleanup,daemon_manager,daemon_middleware,daemon_worker,dashboard_views,db_compat,http_trigger_middleware,middleware,models,registries,settings,subprocess_executor,subprocess_middleware,urls,views,worker_claiming,worker_process,worker_registry}.py
    - src/sqlery/core/daemon.py
    - src/sqlery/rate_limit_utils.py
    - src/sqlery/django_sqlery/utils.py
    - src/sqlery/core/worker.py
decisions:
  - "Used sys.modules identity-aliasing in webhooks BC stub (not re-export *) to preserve patch.object semantics for tests/unit/test_webhooks.py"
  - "Resolved backend+queues outside the worker loop (one-time cost) in arity fix"
metrics:
  duration: ~25 min
  completed: 2026-05-15
---

# Phase 04 Plan 01: CLEAN-01/02/03/04 + worker_process.py:71 Arity Fix — Summary

Omnibus cleanup plan: fixes a live `ModuleNotFoundError` for `sqlery.django_sqlery.webhooks`, date-stamps 20 backward-compat stubs + 11 dead commented blocks, verifies async_worker.py marker is in place, and closes the deferred Phase-03 arity bug at `django_sqlery/worker_process.py:71`.

## What Was Built

### Task 1 — CLEAN-04 (commit 52ff9aa)
Pre-execution probe confirmed the bug was live: `from sqlery.django_sqlery.webhooks import send_webhook_with_retry` raised `ModuleNotFoundError`. The fix moves the canonical `webhooks.py` into `sqlery/django_sqlery/` (where its Django coupling — `F`, `.objects.filter`, `.save(update_fields=…)` — belongs) and replaces the old path with a dated BC stub. The stub uses `sys.modules[__name__] = canonical_module` identity-aliasing rather than `from … import *`, so that `patch.object(webhooks_mod, "requests", …)` in `tests/unit/test_webhooks.py` continues to operate on the same binding the canonical code reads. Regression test (`tests/unit/test_clean04_webhook_import.py`) asserts both import paths resolve to the same callable.

### Task 2 — CLEAN-01 (commit 3a4d103)
Inserted a single comment line (`# Remove after 2027-05-14 …`) immediately after the existing `# #CLEANUP:` marker in all 20 BC stubs under `src/sqlery/`. No re-export lines touched.

### Task 3 — CLEAN-02 verify + CLEAN-03 (commit dd8fc35)
- CLEAN-02 verification: `src/sqlery/async_worker.py:2` already carries `# Remove after 2026-11-14.` — no edit.
- CLEAN-03: AST-scanned the 4 hotspots for contiguous (3+ line) commented-out code blocks containing Python tokens; inserted `# #CLEANUP 2026-05-14: dead code below — Remove after 2027-05-14.` before each.

| File | Markers added |
|------|---------------|
| `src/sqlery/core/daemon.py` | 4 |
| `src/sqlery/rate_limit_utils.py` | 2 |
| `src/sqlery/django_sqlery/utils.py` | 3 |
| `src/sqlery/core/worker.py` | 2 |

Total: **11 markers**, well under the 20-cap. No commented blocks deleted; no live code modified.

### Task 4 — Arity fix (commit eb5f5f6)
The call at `django_sqlery/worker_process.py:71` was `claim_next_job_with_queue_priority(worker)` — but the canonical signature in `core/claiming.py:178` requires `(worker, backend, queues)`. Fix: resolve `backend = get_backend()` and `queues = get_setting("WORKER_QUEUES", ["default"])` once outside the loop, then pass all three. Same pattern as Phase 03 commit `f22049d`. Regression test uses `ast.parse` to walk the source and assert every call to `claim_next_job_with_queue_priority` in the file passes ≥3 args.

## Commits

| Hash | Message |
|------|---------|
| `52ff9aa` | fix(04-01): CLEAN-04 — move webhooks.py to django_sqlery/, leave dated BC stub |
| `3a4d103` | docs(04-01): CLEAN-01 — date-stamp 20 BC stubs with Remove after 2027-05-14 |
| `dd8fc35` | docs(04-01): CLEAN-02 verify + CLEAN-03 — date-stamp commented-block hotspots |
| `eb5f5f6` | fix(04-01): worker_process.py:71 arity bug — pass (worker, backend, queues) |

## Verification

```text
uv run pytest tests/unit/test_clean04_webhook_import.py \
              tests/unit/test_worker_process_arity.py \
              tests/unit/test_webhooks.py -v
→ 42 passed, 1 xfailed in 0.13s
```

`grep -l "Remove after 2027-05-14" src/sqlery/*.py | wc -l` → **20**.
`grep "Remove after 2026-11-14" src/sqlery/async_worker.py` → **1 line** (CLEAN-02).
`git status` shows **no deleted files**.

## Deviations from Plan

**1. [Rule 1 - Bug fix during execution] BC stub strategy upgraded from `from … import *` to `sys.modules` aliasing**
- **Found during:** Task 1 verify step.
- **Issue:** `tests/unit/test_webhooks.py` patches `webhooks_mod.requests` where `webhooks_mod = sqlery.webhooks`. After moving the file, the function reads `requests` from the canonical module's namespace, but the test patches the stub's namespace — patches no-op → real HTTPS requests escape → test fails on DNS.
- **Fix:** Stub does `sys.modules[__name__] = sqlery.django_sqlery.webhooks` so the two module objects are identical; `patch.object` on either name targets the same binding.
- **Files modified:** `src/sqlery/webhooks.py`
- **Commit:** `52ff9aa`

**2. [Plan instruction] Default removal date used per PLAN.md grep contract**
- The orchestrator prompt said default removal date is `2027-05-15`, but the PLAN.md `verify` blocks grep for `Remove after 2027-05-14` (the date pinned at plan write time, 2026-05-14 + 12mo). Using `2027-05-14` to satisfy the plan's automated verification. No semantic difference at the day grain.

**3. CLEAN-03 hotspot count**
- Plan capped CLEAN-03 at 20 marker insertions across 4 files. Discovered only 11 qualifying blocks (3+ line contiguous, code-token-bearing). All 11 marked; no follow-up CLEAN-03b plan needed.

## Task 5 (Human-Verify Checkpoint)

Per orchestrator instructions for parallel-executor mode, the human-verify checkpoint at Task 5 is converted to **automated verification** (executed inline above): all greps pass, all regression tests green, `git status` shows no deletions. Reviewer can replay the steps from `<how-to-verify>` in the plan against the four commits listed above.

## Known Stubs

None.

## Threat Flags

None — this plan tightened existing surfaces (BC stub, arity bug) rather than introducing new ones. Phase 04 Wave-2 SEC plans will patch the same files for CSRF/SSRF.

## Self-Check: PASSED

- `src/sqlery/django_sqlery/webhooks.py` — FOUND (contains `def send_webhook_with_retry`)
- `src/sqlery/webhooks.py` — FOUND (BC stub, sys.modules aliasing, `Remove after 2027-05-14` marker)
- `tests/unit/test_clean04_webhook_import.py` — FOUND, 3 tests passing
- `tests/unit/test_worker_process_arity.py` — FOUND, 2 tests passing
- Commits `52ff9aa`, `3a4d103`, `dd8fc35`, `eb5f5f6` — all present in `git log`
- 20 BC stubs with `Remove after 2027-05-14` — grep confirmed
- `src/sqlery/async_worker.py` `Remove after 2026-11-14` — grep confirmed
- 4 hotspot files with `Remove after 2027-05-14` — grep confirmed
- `git status` — no deleted files
