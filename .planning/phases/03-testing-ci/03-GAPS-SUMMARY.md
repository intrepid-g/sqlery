---
phase: 03
plan: GAPS
subsystem: testing-ci
type: gap-closure
tags: [testing, ci, hotfix]
date: 2026-05-14
---

# Phase 03 Gap-Closure Summary

Surgical follow-ups raised by the Phase 03 verifier. Three fixes in scope;
two required code changes, one is documentation-only.

## Fix 1 — 3 failing unit tests (one root cause)

**Symptom (per verifier):** Three tests failed under the Django access
guard:
- `tests/unit/test_django_backend.py::TestEnqueueAndClaim::test_claim_job_returns_queued_then_running`
- `tests/unit/test_django_backend.py::TestEnqueueAndClaim::test_claim_job_none_when_empty`
- `tests/unit/test_worker.py::TestForkLifecycle::test_parent_branch_records_child_pid_and_waits`

**Actual root cause (confirmed by running the tests locally):** Not a
Django access-guard issue — both `TestEnqueueAndClaim` and the worker
test were already correctly marked. The real failure was a
`TypeError: claim_next_job_with_queue_priority() missing 1 required
positional argument: 'backend'`.

`claim_next_job_with_queue_priority` was promoted from
`django_sqlery.worker_claiming` to `core.claiming` and the signature
was changed to `(worker, backend, queues, ...)` — but the only
remaining call inside `DjangoBackend.claim_job` still passed
`(worker_row, queues=queues)`. The worker test exercises the same
code path indirectly via the fork lifecycle, so it failed for the
same reason.

**Fix:** Pass `self` (the DjangoBackend instance) as the `backend`
argument in `src/sqlery/django_sqlery/backend.py:160`.

**Files modified:**
- `src/sqlery/django_sqlery/backend.py` (1-line change)

**Commit:** `f22049d`

**Verification:** All three named tests now pass:

```
tests/unit/test_django_backend.py::TestEnqueueAndClaim::test_claim_job_returns_queued_then_running PASSED
tests/unit/test_django_backend.py::TestEnqueueAndClaim::test_claim_job_none_when_empty             PASSED
tests/unit/test_worker.py::TestForkLifecycle::test_parent_branch_records_child_pid_and_waits       PASSED
```

**Note:** A second site with the same bug exists at
`src/sqlery/django_sqlery/worker_process.py:71`
(`claim_next_job_with_queue_priority(worker)` — missing both `backend`
and `queues`). This was *not* flagged by the verifier and is outside
the documented scope of the three failing tests, so it is logged here
as a deferred follow-up rather than fixed in this gap-closure pass.

## Fix 2 — tests/chaos/test_property_based.py collection break

**Symptom:** Module fails to collect with
`cannot import name 'serialize_job_arguments' from 'sqlery.utils'`.
This breaks the strict PostgreSQL CI step in `.github/workflows/test.yml`
which has no `--ignore` tolerance.

**Investigation:** `grep -rn "def serialize\|def deserialize" src/sqlery/`
returns zero hits — the helpers were genuinely removed during Phase 1
dead-code consolidation. Argument flow is now plain dicts through the
backend layer, with no central serializer/deserializer pair.

**Decision:** Option B from the gap brief — convert the module to a
dated stub (`pytest.skip(..., allow_module_level=True)` with a
`#CLEANUP 2026-05-14` marker per CLAUDE.md `feedback_dead_code`).
A rewrite against the current job-argument pipeline is captured in
the module docstring as the cleanup TODO.

**Files modified:**
- `tests/chaos/test_property_based.py` (full file replaced with stub)

**Commit:** `0695e90`

**Verification:**

```
$ uv run pytest tests/chaos/test_property_based.py --collect-only
collected 0 items / 1 skipped
========================= no tests collected in 0.06s ==========================
exit=0
```

## Fix 3 — coverage gate (DO NOTHING)

The `fail_under = 13` baseline in `pyproject.toml` is intentionally
deferred technical debt. The line is annotated with a `[FOLLOWUP]`
tag and an explanatory comment in `pyproject.toml`; nothing further
is required in this gap-closure pass.

**Path to 70% (documented for the eventual follow-up):**
1. Resolve the ~196 test-collection errors caused by Django
   test-fixture pollution (most are pytest-django settings/`db`
   fixture mismatches in cross-mode test files).
2. Re-run the full suite with coverage enabled across both Django
   and standalone modes.
3. Bump `fail_under` in a single follow-up commit once the real
   coverage number is known and ratchet from there.

No files modified. No commit needed for this fix.

## Verifier Success Criteria Check

- [x] All 3 failing unit tests now pass
- [x] `tests/chaos/test_property_based.py` collects without error
  (dated stub, exit 0, "0 items / 1 skipped")
- [x] 03-GAPS-SUMMARY.md written and committed
- [x] No regression to `pyproject.toml` (untouched; django>=5.2,
  aiosqlite, greenlet, fail_under=13 all preserved)
- [x] No modifications to STATE.md or ROADMAP.md

## Deferred / Out-of-Scope Items

- **`worker_process.py:71`** also calls
  `claim_next_job_with_queue_priority(worker)` with the wrong arity.
  Not covered by the three named failing tests; defer to a dedicated
  worker-process fix.
- **Property-based test rewrite.** Module is stubbed; needs new
  property tests written against the current dict-based argument
  pipeline (#CLEANUP 2026-05-14).
- **Coverage ratchet to 70%.** Tracked via `[FOLLOWUP]` tag in
  `pyproject.toml`; requires fixing 196 collection errors first.

## Self-Check: PASSED

- FOUND: `src/sqlery/django_sqlery/backend.py` (commit `f22049d`)
- FOUND: `tests/chaos/test_property_based.py` (commit `0695e90`)
- FOUND: commit `f22049d` in `git log`
- FOUND: commit `0695e90` in `git log`
