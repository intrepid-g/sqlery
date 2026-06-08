---
phase: 08-standalone-lease-parity
fixed_at: 2026-06-08T00:00:00Z
review_path: .planning/phases/08-standalone-lease-parity/08-REVIEW.md
iteration: 2
findings_in_scope: 1
fixed: 1
skipped: 0
status: all_fixed
---

# Phase 08: Code Review Fix Report

**Fixed at:** 2026-06-08
**Source review:** .planning/phases/08-standalone-lease-parity/08-REVIEW.md
**Iteration:** 2

**Summary:**
- Findings in scope (critical_warning): 1
- Fixed: 1
- Skipped: 0
- Info findings (out of scope): 3 — recorded below for completeness

## Fixed Issues

### WR-01: SQLite own-live re-claim CAS lacks `synchronize_session=False`, unlike its sibling expired-takeover CAS

**Files modified:** `src/sqlery/fastapi_sqlery/backend.py`
**Commit:** fef24a4
**Applied fix:** Added `.execution_options(synchronize_session=False)` to the
own-live re-claim CAS `update(DaemonLease)` statement inside the version-CAS
branch of `_claim_one_lease`, so it shares the same execution rule as the
sibling expired-takeover CAS. An explanatory comment was added noting this
matches the expired-takeover CAS and future-proofs the statement against any
later-added datetime/JSON predicate that would otherwise trip the ORM
synchronize evaluator. The change is purely additive (a missing option, not a
wrong line); no existing lines were deleted or rewritten, consistent with house
convention.

**Verification:**
- Tier 1: re-read modified section; fix present, surrounding code intact.
- Tier 2: `python -c "import ast; ast.parse(...)"` -> PARSE_OK.
- Regression: `uv run --active pytest tests/unit/test_sqlalchemy_backend_sync.py -q`
  -> 84 passed, 7 skipped, 2 xfailed. No regression.

## Skipped Issues

The following findings are Info-tier (IN-*) and out of scope for the
`critical_warning` fix scope. Recorded here for completeness.

### IN-01: Duplicated naive->aware normalization ternary at 4+ sites

**File:** `src/sqlery/fastapi_sqlery/backend.py:344-348, 392-396`; `src/sqlery/core/models.py:167, 187`
**Reason:** skipped -- Info severity, out of scope for critical_warning. The
suggested refactor (extract a module-level `_aware(dt)` helper) is a
non-functional cleanup, not a correctness fix.
**Original issue:** The `dt if dt.tzinfo else dt.replace(tzinfo=UTC)` idiom is
copy-pasted across both lease branches and `models.py`; correctness-neutral.

### IN-02: `determine_claim_strategy` `basic_lock` is a named-but-unimplemented strategy

**File:** `src/sqlery/fastapi_sqlery/backend.py:21-39` (consumed at 303, 367-442)
**Reason:** skipped -- Info severity, out of scope for critical_warning.
Correctness-neutral (MySQL silently uses the version-CAS path); resolving it
requires a design decision (collapse to two strategies vs. implement a blocking
`SELECT ... FOR UPDATE` branch) better left to a human.
**Original issue:** `basic_lock` has no distinct branch; it falls through to the
version-CAS path. The third strategy name is misleading.

### IN-03: `claim_queue_leases` commits per queue; mid-loop exception leaves earlier queues claimed but discards partial result

**File:** `src/sqlery/fastapi_sqlery/backend.py:264-274`
**Reason:** skipped -- Info severity, out of scope for critical_warning. The
reviewer explicitly marked it "Acceptable as-is given TTL recovery"; it matches
Django's per-queue commit model and leases self-heal via TTL expiry within
`lease_secs`.
**Original issue:** If queue N raises after queues 0..N-1 committed, the
exception propagates and the partial `claimed` list is never returned.

---

_Fixed: 2026-06-08_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 2_
