---
phase: 09-core-shared-scheduler-election
verified: 2026-06-08T09:30:35Z
status: passed
score: 7/7 must-haves verified
overrides_applied: 0
re_verification:
  previous_status: none
  previous_score: n/a
---

# Phase 9: Core-Shared Scheduler Election Verification Report

**Phase Goal:** A bare worker self-elects as scheduler-leader by participating in the existing per-queue lease scheme, firing cron only for queues it holds, while a running daemon stays authoritative.
**Verified:** 2026-06-08T09:30:35Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | A bare `sqlery-worker` claims/renews the per-queue lease for every queue in `self.queues` each poll cycle (ELECT-01) | ✓ VERIFIED | worker.py:506-510 initial claim; loop renew worker.py:540-542 + re-claim unowned worker.py:543-558. Test `test_worker_claims_or_renews_lease_for_every_configured_queue` asserts claim records cover `{default, reports}` AND both due tasks fire (firing requires holding the lease). PASSED. |
| 2 | The worker runs `scheduler.run_due_tasks` only for the queues it holds the lease for (ELECT-02) | ✓ VERIFIED | worker.py:560 `run_due_tasks(queue_names=owned_queues)`; scheduler.py:48-49 filters `t.queue_name in queue_names`. Test `test_worker_fires_cron_only_for_held_queues`: held `a` fires (1 job), foreign-held `b` does not (0 jobs). PASSED. |
| 3 | When a live daemon/holder owns a queue lease, the worker does not win it and does not fire that queue's cron (ELECT-05) | ✓ VERIFIED | Enforced by Phase-8 lease primitive — `FakeBackend.claim_queue_leases` (conftest.py:620-633) skips `expires_at > now and daemon_id != holder`. Test `test_live_foreign_lease_keeps_worker_from_scheduling`: foreign `daemon_other` lease untouched, no job enqueued. PASSED. |
| 4 | A dead leader's lease expires after `poll_interval*3` and another worker re-claims it next cycle (ELECT-06) | ✓ VERIFIED | TTL `lease_secs = self.poll_interval * 3` (worker.py:504, mirrors daemon `check_interval * 3`). Re-claim of `unowned` worker.py:543-558. Test `test_expired_lease_is_taken_over_and_cron_fires`: PAST `expires_at` → worker re-claims `default` and fires cron. PASSED. |
| 5 | On SIGTERM/SIGINT graceful shutdown the worker releases the leases it holds (ELECT-03) | ✓ VERIFIED | `release_queue_leases(sorted(owned_queues), self.worker_id)` in existing `finally:` (worker.py:646-649); `owned_queues` initialized before `try:` (worker.py:506). Test `test_held_leases_released_on_graceful_shutdown`: `default` gone from `_leases`, release recorded under worker id. PASSED. |
| 6 | The job-claim path `backend.claim_job(self.queues, ...)` is unchanged — all workers claim/execute from all queues (ELECT-07) | ✓ VERIFIED | worker.py:566 `claim_job(self.queues, self.worker_id)` byte-for-byte unchanged; grep confirms it is NOT scoped to `owned_queues`. Test `test_job_claim_path_uses_full_queue_set_regardless_of_leases` asserts `claim_job` called with full `['a','b']` in both holds-all AND holds-none scenarios. PASSED. |
| 7 | Election errors (claim/renew/run_due_tasks/release) are caught, logged, and the worker loop continues — election never crashes the worker | ✓ VERIFIED | Initial claim try/except → fallback `set()` (worker.py:511-515); per-cycle election step in own `try/except ... exc_info=True` (worker.py:538-564); release wrapped in try/except (worker.py:646-649). `claim_job` outside the election try so a scheduling failure never blocks job execution. |

**Score:** 7/7 truths verified

### ELECT-04 (headline, from Plan 02 must_haves)

| Truth | Status | Evidence |
|-------|--------|----------|
| A bare worker (no daemon) fires a due cron `ScheduledTask` for a held queue (ELECT-04) | ✓ VERIFIED | Test `test_bare_worker_fires_due_cron_for_held_queue` constructs ONLY a `WorkerProcess` (no daemon object anywhere), seeds a due task on `default`, runs one election cycle, asserts the worker claimed `default` and exactly one job was enqueued. PASSED. |

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/sqlery/core/worker.py` | Per-queue election lifecycle in `WorkerProcess.run` + release on shutdown; contains `claim_queue_leases` | ✓ VERIFIED | Substantive (not stub): top-level `from .scheduler import Scheduler` (line 19), Scheduler init + TTL + initial claim (501-518), per-cycle renew/re-claim/fire (538-564), release in finally (646-649). Imports cleanly (`import sqlery.core.worker` exits 0, no circular import). Wired: delegates to `self.backend` (DatabaseBackend ABC) → mode-agnostic across Django + standalone. |
| `tests/unit/test_worker.py` | `TestWorkerSchedulerElection` covering ELECT-01..07 against in-memory FakeBackend | ✓ VERIFIED | Class at line 410 with 7 tests + 4 helpers. Asserts against real `FakeBackend` state (`_leases`, `_jobs`, recorded `calls`) produced by actual `WorkerProcess.run` — spies wrap real lease methods, not mocked returns. All 7 PASS. |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|----|--------|---------|
| worker.py | `backend.claim_queue_leases / renew_queue_leases / release_queue_leases` | `run` election step + finally release | ✓ WIRED | All three present (lines 507, 540, 546, 647) on `self.backend`. |
| worker.py | `scheduler.run_due_tasks(queue_names=owned)` | fire cron only for held queues | ✓ WIRED | Line 560 passes `owned_queues` set; scheduler.py:48-49 honors membership filter. |
| worker.py | `.scheduler.Scheduler` | top-level import | ✓ WIRED | Line 19, no inline import (project rule honored). |
| test_worker.py | `WorkerProcess.run` election wiring | drive bounded poll cycles with FakeBackend leases + due tasks | ✓ WIRED | `_run_one_election_cycle` drives real `run()`; cron job + claim record are load-bearing assertions. |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| worker.py election | `owned_queues` | `backend.claim_queue_leases(...)` real return (Phase 8) | Yes — gated by real lease semantics (skips live foreign leases) | ✓ FLOWING |
| worker.py cron fire | `jobs` | `scheduler.run_due_tasks(queue_names=owned_queues)` → `_enqueue_for_scheduled_task` | Yes — enqueues real jobs; proven by `_job_count_for_task` assertions in tests | ✓ FLOWING |

### Behavioral Spot-Checks / Probe Execution

| Probe | Command | Result | Status |
|-------|---------|--------|--------|
| Election test class | `uv run --active pytest tests/unit/test_worker.py::TestWorkerSchedulerElection -q` | 7 passed in 0.49s | ✓ PASS |
| Full worker suite (regression) | `uv run --active pytest tests/unit/test_worker.py -q` | 31 passed (24 pre-existing + 7 new) in 0.43s | ✓ PASS |
| Import sanity | `python -c "import sqlery.core.worker"` | exit 0, no circular import | ✓ PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| ELECT-01 | 09-01, 09-02 | Claim/renew lease for every configured queue each cycle | ✓ SATISFIED | Truth 1 |
| ELECT-02 | 09-01, 09-02 | Run due cron only for held queues | ✓ SATISFIED | Truth 2 |
| ELECT-03 | 09-01, 09-02 | Release held leases on graceful shutdown | ✓ SATISFIED | Truth 5 |
| ELECT-04 | 09-02 | Bare worker fires cron with no daemon (both modes) | ✓ SATISFIED (core) | ELECT-04 test; cross-matrix CI proof is Phase 11 / PARITY-04 |
| ELECT-05 | 09-01, 09-02 | Daemon stays authoritative; worker defers | ✓ SATISFIED | Truth 3 |
| ELECT-06 | 09-01, 09-02 | Failover within one TTL when leader dies | ✓ SATISFIED | Truth 4 |
| ELECT-07 | 09-01, 09-02 | Lease gates cron only, never job execution | ✓ SATISFIED | Truth 6 |

All 7 phase requirement IDs accounted for. No orphans — REQUIREMENTS.md maps ELECT-01..07 to Phase 9 and all appear in the plans' `requirements` frontmatter.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| src/sqlery/core/worker.py | various (executor class, untouched lines) | Single-quote string style flagged by `black --check` | ℹ️ Info | PRE-EXISTING — `black --diff` confirms NO reformat lands on any phase-added line (election block 495-650 already double-quote/black-compliant). Not phase-introduced. Not a blocker. |

No debt markers (`TBD`/`FIXME`/`XXX`) in either modified file.

### Human Verification Required

None. All observable truths are proven by deterministic, DB-free behavioral unit tests that exercise the real `WorkerProcess.run` election path against `FakeBackend` lease semantics. No visual, real-time, or external-service behavior requires human confirmation at this phase. The end-to-end live-process and cross-matrix proof is the explicit scope of Phase 11 (PARITY-04) and is correctly deferred there.

### Deferred Items

The "in both Django and standalone modes" clause of ELECT-04 / Success Criterion 1 is satisfied at the mechanism level in Phase 9 (the wiring is mode-agnostic — it touches only `WorkerProcess.run` and delegates entirely to the `DatabaseBackend` ABC implemented for both modes in Phase 8). The cross-matrix CI *proof* is intentionally not a Phase 9 deliverable.

| # | Item | Addressed In | Evidence |
|---|------|--------------|----------|
| 1 | End-to-end / cross-matrix proof that bare-worker cron fires across `{Django, standalone} × {SQLite, Postgres}` | Phase 11 | Phase 11 SC: "An end-to-end bare-worker test proves cron fires with only `sqlery-worker` processes and no daemon" + "Every behavioral test asserts identical outcomes across `{Django, standalone} × {SQLite, Postgres}`" (PARITY-04, PARITY-05) |
| 2 | Cron-semantics hardening (atomic advance, drift, jitter, exactly-once under two-leader overlap) | Phase 10 | T-09-03 deferred per plan threat register; CRON-01..04 mapped to Phase 10 |

### Gaps Summary

No gaps. The phase goal is achieved: `WorkerProcess.run` now contains a complete per-queue scheduler-election lifecycle ported from `DaemonManager.run` — initial claim (before `try:`), per-cycle renew + re-claim-expired + fire-held-queue cron, and release in `finally` — using the worker's own identity and a `poll_interval * 3` TTL, with no config knob, no reserved key, and no new table. Every election call is error-isolated. The job-claim path (`claim_job(self.queues, ...)`) is byte-for-byte unchanged (ELECT-07). All 7 requirement IDs are covered by 7 passing behavioral tests against real lease semantics, and the full worker suite passes with no regressions. The mechanism is mode-agnostic; the cross-matrix CI proof is correctly scoped to Phase 11.

---

_Verified: 2026-06-08T09:30:35Z_
_Verifier: Claude (gsd-verifier)_
