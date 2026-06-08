---
phase: 09-core-shared-scheduler-election
plan: 02
subsystem: worker
tags: [worker, scheduler, leader-election, tests, cron, bare-worker]
requires:
  - "WorkerProcess.run election wiring (Plan 09-01): per-queue claim/renew/release + run_due_tasks(queue_names=owned)"
  - "FakeBackend per-queue lease semantics + get_due_scheduled_tasks (tests/unit/conftest.py)"
provides:
  - "TestWorkerSchedulerElection: behavioral proof of ELECT-01..07 against the in-memory FakeBackend"
  - "Bounded one-cycle test harness for WorkerProcess.run (no real-TTL sleeps, DB-free)"
affects:
  - "tests/unit/test_worker.py"
tech-stack:
  added: []
  patterns:
    - "Drive WorkerProcess.run for exactly one election pass by patching claim_job to flip shutdown_requested + return None"
    - "Patch time.sleep to a no-op; simulate TTL expiry via a PAST expires_at (never sleep a real TTL)"
    - "Spy lease calls (claim/renew/release) via monkeypatch wrappers since FakeBackend does not _record them"
    - "Cron-job firing is the load-bearing proof of held-lease ownership (run_due_tasks fires only for held queues)"
key-files:
  created: []
  modified:
    - "tests/unit/test_worker.py"
decisions:
  - "Assert election via the durable claim record + freshly enqueued cron job, NOT post-cycle _leases state — run()'s finally: releases held leases on shutdown (ELECT-03), so _leases no longer reflects what the worker held during the cycle."
  - "ELECT-01 proven by seeding a due task on EVERY configured queue and asserting BOTH fire (firing requires the lease), in addition to checking the claim records cover all queues."
  - "ELECT-07 holds-none scenario uses a freshly constructed FakeBackend (not the autouse fixture) so live foreign leases on both queues can be pre-seeded without the worker ever owning them."
metrics:
  duration: "~12m"
  completed: 2026-06-08
  tasks: 2
  files: 1
---

# Phase 9 Plan 02: Core-Shared Scheduler Election Tests Summary

Added `TestWorkerSchedulerElection` to `tests/unit/test_worker.py` — seven fast, DB-free unit tests that prove the Phase 9 leader-election wiring (Plan 09-01) end-to-end against the in-memory `FakeBackend`. The headline test demonstrates ELECT-04: a bare `WorkerProcess` (no daemon constructed anywhere) self-elects as scheduler-leader for a queue it holds and fires a due cron `ScheduledTask` for it. Supporting tests pin the rest of the election contract: per-cycle claim/renew across all configured queues (ELECT-01), fire-only-held-queues (ELECT-02), live-daemon/foreign authority (ELECT-05), TTL-bounded failover takeover (ELECT-06), the unchanged full-queue job-claim path (ELECT-07), and lease release on graceful shutdown (ELECT-03).

## What Was Built

### Shared test harness (module-level helpers)
- `_run_one_election_cycle(wp, monkeypatch)` — drives `WorkerProcess.run` through exactly one election pass then exits. Patches `time.sleep` to a no-op, neutralizes `close_old_connections`, and patches `claim_job` to record the call, set `wp.shutdown_requested = True`, and return `None`. The worker performs its full election step (renew/re-claim leases + `run_due_tasks`), sees no job, and the bounded poll-sleep guard (`while elapsed < poll_interval and not shutdown_requested`) exits immediately into `run()`'s `finally:` (lease release). Returns a `{"claim": [...], "renew": [...], "release": [...]}` dict of `(queues, daemon_id)` tuples captured via monkeypatched lease-call wrappers — necessary because `FakeBackend` does not `_record` lease calls and `run()` clears held leases from `_leases` on shutdown.
- `_seed_due_task` — seeds an enabled `ScheduledTask` with `next_run_at` in the past.
- `_job_count_for_task` / `_claimed_queues` — read real `fake_backend._jobs` and the captured claim records.

### Task 1 — bare-worker cron + claim/renew + fire-held-only (commit `dc29b4c`)
- `test_bare_worker_fires_due_cron_for_held_queue` (ELECT-04): constructs ONLY a `WorkerProcess`, seeds a due task on `default`; asserts the worker claimed `default` and exactly one job was enqueued for the task.
- `test_worker_claims_or_renews_lease_for_every_configured_queue` (ELECT-01): worker on `['default','reports']` with a due task on each; asserts the claim records cover `{default, reports}` and BOTH tasks fired (firing requires holding the lease).
- `test_worker_fires_cron_only_for_held_queues` (ELECT-02): worker on `['a','b']`, live foreign lease pre-seeded on `b`; due tasks on both; asserts `a` fired and `b` did not.
- `test_live_foreign_lease_keeps_worker_from_scheduling` (ELECT-05): live foreign lease (`daemon_other`) on `default`; asserts the foreign holder is untouched and no job was enqueued.

### Task 2 — TTL failover + unchanged job-claim + release on shutdown (commit `377fbf5`)
- `test_expired_lease_is_taken_over_and_cron_fires` (ELECT-06): foreign lease on `default` with a PAST `expires_at` (dead leader, no real sleep); asserts the worker re-claimed `default` and fired the due cron. Comment notes the production failover window is bounded by the lease TTL (`poll_interval * 3`, Plan 01), simulated here via expiry.
- `test_job_claim_path_uses_full_queue_set_regardless_of_leases` (ELECT-07): asserts `claim_job` was called with the full `['a','b']` in BOTH the holds-all scenario and a holds-none scenario (fresh `FakeBackend` with live foreign leases on both queues) — leases gate cron-firing only, never execution.
- `test_held_leases_released_on_graceful_shutdown` (ELECT-03): worker on `['default']` with no contention; asserts the worker held `default` during the cycle, `default` is gone from `_leases` after shutdown, and `release_queue_leases` was called for `default` under the worker's id.

## Requirements Satisfied
- ELECT-01 — claim/renew per-queue lease every cycle for all `self.queues` (claim records cover all queues + both queues' cron fire).
- ELECT-02 — `run_due_tasks` fires only for held queues (held `a` fires, foreign-held `b` does not).
- ELECT-03 — held leases released on graceful shutdown (`finally:` path), removed from `_leases`.
- ELECT-04 — bare worker (no daemon) fires a due cron for a held queue.
- ELECT-05 — live foreign/daemon lease stays authoritative; worker does not take over or fire.
- ELECT-06 — expired-lease takeover + cron fire (failover within one TTL, expiry simulated).
- ELECT-07 — `claim_job` uses the full configured queue set whether the worker holds all leases or none.

## Verification
- `uv run --active pytest tests/unit/test_worker.py::TestWorkerSchedulerElection -q` — 7 passed.
- `uv run --active pytest tests/unit/test_worker.py -q` — 31 passed (24 pre-existing + 7 new), no regressions, ~0.44s total (DB-free, no real-TTL sleeps).
- `black tests/unit/test_worker.py` — formatted (test file conforms).
- `ruff check tests/unit/test_worker.py` — only finding is the PRE-EXISTING unused `import sys` at line 25 (present in `HEAD:tests/unit/test_worker.py`, on a line this plan did not touch). Out of scope per the plan's scope boundary; not introduced by this plan.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Assert election via claim records + cron firing instead of post-cycle `_leases` state**
- **Found during:** Task 1 (RED run)
- **Issue:** The plan's behavior notes suggested asserting `fake_backend._leases[queue]['daemon_id'] == wp.worker_id` after the cycle. But `WorkerProcess.run`'s `finally:` block releases all held leases on graceful shutdown (ELECT-03, implemented in Plan 01), so by the time `run()` returns, the worker-owned leases are popped from `_leases` — making the direct post-cycle lease assertion fail with `KeyError`.
- **Fix:** Capture lease ownership DURING the cycle via monkeypatched `claim_queue_leases`/`renew_queue_leases`/`release_queue_leases` spy wrappers in `_run_one_election_cycle` (FakeBackend does not `_record` lease calls), and treat the freshly enqueued cron job as the load-bearing proof of held-lease ownership (since `run_due_tasks` fires only for held queues). Foreign-lease assertions still read `_leases` directly because foreign leases are NOT released by the worker's `finally:` (release only pops leases owned by `self.worker_id`).
- **Files modified:** tests/unit/test_worker.py
- **Commit:** dc29b4c

This is a test-design correction, not a production change — it makes the tests assert against the real, correct post-Plan-01 behavior (release-on-shutdown) rather than a pre-release snapshot.

## Threat Surface
No new security-relevant surface. Tests drive `WorkerProcess` against the in-memory `FakeBackend` only — no real DB, network, or external service. T-09-T1 (false-green) is mitigated: every assertion reads real `FakeBackend` state (`_jobs`, `_leases`, captured lease calls, recorded `claim_job` args) produced by the actual `WorkerProcess.run` election path, so a test cannot pass unless Plan 01's wiring runs. T-09-T2 (flaky/slow CI) is mitigated: TTL expiry is simulated via a PAST `expires_at`, `time.sleep` is patched to a no-op, and the loop is bounded to one cycle — the full file runs in ~0.44s. No package installs; no new dependencies (CLAUDE.md).

## Notes
Cross-matrix `{Django, standalone} × {SQLite, Postgres}` parity proof is explicitly deferred to Phase 11. Cron-semantics hardening (atomic enqueue+advance, drift, jitter, exactly-once-under-overlap) is deferred to Phase 10 and intentionally NOT tested here.

## Self-Check: PASSED
- FOUND: tests/unit/test_worker.py
- FOUND commit: dc29b4c (Task 1)
- FOUND commit: 377fbf5 (Task 2)
