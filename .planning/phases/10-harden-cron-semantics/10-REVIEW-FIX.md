---
phase: 10-harden-cron-semantics
fixed_at: 2026-06-08T10:22:26Z
review_path: .planning/phases/10-harden-cron-semantics/10-REVIEW.md
iteration: 1
findings_in_scope: 7
fixed: 4
skipped: 3
status: partial
---

# Phase 10: Code Review Fix Report

**Fixed at:** 2026-06-08T10:22:26Z
**Source review:** .planning/phases/10-harden-cron-semantics/10-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope (Critical + Warning): 7
- Fixed: 4
- Skipped: 3 (in-scope warnings that were doc-only / out-of-scope code paths)

All targeted tests green after fixes: `tests/unit tests/test_atomic_scheduler.py
tests/test_scheduler_drift_jitter.py tests/test_core_standalone.py` —
453 passed, 14 skipped, 3 xfailed.

## Fixed Issues

### CR-01: Clamp-cap exhaustion returns a past `next_run_at`, causing an immediate re-fire busy-loop

**Files modified:** `src/sqlery/core/scheduler.py`, `tests/test_scheduler_drift_jitter.py`
**Commit:** 4cbfa26
**Status:** fixed: requires human verification (logic fix — recovery path correctness)
**Applied fix:** In `calculate_next_run`, when the future-clamp loop hits
`_MAX_CLAMP_ITERATIONS` with `candidate <= now`, instead of returning the stale past
candidate (which got persisted into `next_run_at` and made the task re-qualify as due
every cycle — a runaway producer), the method now recomputes
`next_cron_occurrence(cron_expression, now)` strictly from the current time so the
persisted value is in the future and the task does not immediately re-fire. The old
`return candidate` / old warning were commented out per project edit rules. Added a
regression test (`test_clamp_cap_exhaustion_does_not_return_past`) that patches
`_MAX_CLAMP_ITERATIONS` low and forces a pathological/unsatisfiable expression, asserting
the returned value is strictly in the future and that the recovery recompute-from-now
path was exercised.

### WR-05: `advance_scheduled_task_if_due` does not re-check `enabled` in the CAS predicate

**Files modified:** `src/sqlery/django_sqlery/backend.py`, `src/sqlery/fastapi_sqlery/backend.py`
**Commit:** 2a61123 (committed together with WR-06 — both harden the same advance method)
**Status:** fixed: requires human verification (TOCTOU/logic guard across both backends)
**Applied fix:** Re-added the `enabled=True` guard the prior `claim_due_scheduled_task`
path had, closing the TOCTOU window where a task disabled between the due-scan and the
advance could still fire. Django: `.filter(id=..., next_run_at=..., enabled=True)`.
Standalone Postgres branch: `if not existing.enabled: return None` under the row lock.
Standalone SQLite predicate-CAS: added `.where(ScheduledTask.enabled == True)`. Old
predicates commented out per project edit rules.

### WR-06: standalone tz normalization dereferences `next_run_at.tzinfo` on a nullable column

**Files modified:** `src/sqlery/fastapi_sqlery/backend.py`
**Commit:** 2a61123 (committed together with WR-05 — both harden the same advance method)
**Status:** fixed
**Applied fix:** Guarded the None case before the `.tzinfo` access in the standalone
Postgres branch: `if existing is None or existing.next_run_at is None: return None`,
treating a concurrently-nulled `next_run_at` (e.g. a `once`-disable between due-scan and
the locked re-read) as a lost CAS instead of raising `AttributeError` inside the
transaction. This restores parity with the Django rowcount-CAS path (NULL equality
matches zero rows). Also reworded the misleading "SQLite returns naive datetimes" comment
to "DB column may be naive (SQLite)" (the IN-01 reword, applied incidentally on the same
touched line).

### WR-01: `_build_queued_job` defaults `retry_backoff` to 0.0 vs canonical 1.0

**Files modified:** `src/sqlery/fastapi_sqlery/backend.py`
**Commit:** 2b802fa
**Status:** fixed
**Applied fix:** Changed `job_kwargs.get("retry_backoff", 0.0)` to
`job_kwargs.get("retry_backoff", 1.0)` to match the model field default and
`DjangoBackend.create_job`, restoring cross-backend parity (the old 0.0 baseline yields
`0.0 * 2**n = 0` backoff for any caller that omits the key). Old line commented out per
project edit rules.

## Skipped Issues

### WR-02: `_build_queued_job` does not set `version`

**File:** `src/sqlery/fastapi_sqlery/backend.py:1079-1104`
**Reason:** skipped: review explicitly notes current behavior is equivalent (both
`_build_queued_job` and `create_job` rely on the model default of `0`). The finding's own
remediation offers "add a comment documenting the intentional reliance on the model
default" as an acceptable alternative; there is no behavioral defect today. Treated as a
documentation/defensive-coding nicety rather than a fix that changes behavior, and left
unchanged to keep the fix set scoped to behavioral hardening. Low risk; recommend the
documenting-comment follow-up if backend parity audits continue.
**Original issue:** `version` is omitted, silently coupling correctness to the model
default staying `0`.

### WR-03: Jitter `time.sleep` runs before the atomic advance, serializing the per-task loop

**File:** `src/sqlery/core/scheduler.py:132-139`
**Reason:** skipped: explicitly out of scope. The review states this is "not a
correctness bug" and "Performance is out of v1 review scope." The sleep-then-CAS ordering
is documented as deliberate. A real fix (per-task jittered `scheduled_at` instead of a
serial sleep, or aggregate-sleep cap) is a behavioral redesign of the jitter mechanism
that belongs in a dedicated performance task, not a review-fix pass. No change applied to
avoid altering the intentional crash-safety semantics.
**Original issue:** With N due tasks and jitter enabled, the daemon thread blocks up to
`N * jitter` seconds serially.

### WR-04: Second scheduler path (`scheduler_tasks.py`) still uses the old non-atomic claim

**File:** `src/sqlery/core/scheduler_tasks.py:43,58`
**Reason:** skipped: out of this phase's scope. The reviewer flagged it as a warning
"rather than blocker" precisely because the file "is out of the stated diff scope." Per
the fix directive, this is a distinct legacy code path (`claim_due_scheduled_task`), and
hardening or removing it requires confirming whether it is live/dead — a scoping decision
that expands beyond the Phase 10 cron-hardening diff. Recorded as skipped with rationale
rather than expanding scope. Recommend a dedicated follow-up to confirm `scheduler_tasks.py`
is dead (and remove it) or migrate it through `advance_scheduled_task_if_due`.
**Original issue:** A parallel firing path retains the old TOCTOU claim race; the
exactly-once guarantee is only partial if both paths are reachable.

### Info findings (IN-01, IN-02, IN-03)

Out of scope for `critical_warning` fix scope; not addressed. Note: IN-01's suggested
comment reword was applied incidentally on a line already touched by the WR-06 fix
(commit 2a61123), but IN-01 is otherwise not separately tracked.

---

_Fixed: 2026-06-08T10:22:26Z_
_Fixer: gsd-code-fixer_
_Iteration: 1_
