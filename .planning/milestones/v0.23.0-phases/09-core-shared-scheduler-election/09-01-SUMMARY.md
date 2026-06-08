---
phase: 09-core-shared-scheduler-election
plan: 01
subsystem: worker
tags: [worker, scheduler, leader-election, leases, cron]
requires:
  - "backend.claim_queue_leases / renew_queue_leases / release_queue_leases (Phase 8, both modes)"
  - "Scheduler.run_due_tasks(queue_names=...) (existing)"
provides:
  - "Per-queue scheduler-election lifecycle inside WorkerProcess.run"
  - "Bare sqlery-worker self-elects as scheduler-leader and fires cron for held queues"
affects:
  - "src/sqlery/core/worker.py (WorkerProcess.run)"
tech-stack:
  added: []
  patterns:
    - "Inline port of daemon lease lifecycle into worker poll loop (no election.py extracted)"
    - "Loop-step error isolation (catch/log/continue) for every election call"
    - "TTL = poll_interval * 3 mirrors daemon check_interval * 3"
key-files:
  created: []
  modified:
    - "src/sqlery/core/worker.py"
decisions:
  - "Inlined the daemon election lifecycle in WorkerProcess.run rather than extracting a shared election.py (lower-risk option, per PATTERNS.md/CONTEXT discretion)."
  - "owned_queues initialized BEFORE the try: so the finally: release always has it in scope (ELECT-03)."
  - "Election placed before claim_job each cycle so cron fires on idle cycles too; claim_job left byte-for-byte unchanged (ELECT-07)."
metrics:
  duration: "~15m"
  completed: 2026-06-08
  tasks: 2
  files: 1
---

# Phase 9 Plan 01: Core-Shared Scheduler Election Summary

Wired the daemon's per-queue scheduler-election lifecycle into `WorkerProcess.run` so a bare `sqlery-worker` self-elects as scheduler-leader: it claims/renews the per-queue lease for every queue in `self.queues` using its own identity each poll cycle, fires `scheduler.run_due_tasks` only for queues it holds, and releases held leases on graceful shutdown. The live-daemon-stays-authoritative guarantee comes for free from the Phase 8 lease primitive, which skips live foreign leases.

## What Was Built

### Task 1 — Initialize election state and import Scheduler (commit `6b3450d`)
- Added top-level `from .scheduler import Scheduler` to the import block (sibling of `from .utils import import_task`). No inline import. Confirmed no circular import (`scheduler.py` imports only `..compat` and `..crontab`).
- Inside `WorkerProcess.run`, immediately after `self._heartbeat('idle')` and BEFORE the main `try:`, added three locals:
  - `scheduler = Scheduler(backend=self.backend)` (uses the already-resolved `self.backend`, not a fresh `get_backend()`).
  - `lease_secs = self.poll_interval * 3` (mirrors daemon's `check_interval * 3`; ≈30s default — ELECT-06 failover window).
  - `owned_queues` — a `set` from an initial `self.backend.claim_queue_leases(self.queues, self.worker_id, self.node_id, self.pid, lease_secs)`, wrapped in try/except that logs and falls back to `set()` on failure.
- `owned_queues` is defined before the `try:` so the `finally:` block can always reference it (no `NameError` even on early crash — ELECT-03).
- Added an info log reporting the worker's scheduler responsibility (`sorted(owned_queues) or 'none yet'`), mirroring the daemon's startup log.
- The worker's `self.worker_id` is passed for the `daemon_id` parameter (PATTERNS.md identity mapping).

### Task 2 — Per-cycle renew/re-claim + fire held-queue cron + release (commit `85159e1`)
- Inserted a per-cycle election step inside the `while not self.shutdown_requested` loop, placed after `self._check_heartbeat()` and before `claim_job`, so it runs every cycle including idle cycles. Wrapped in its own `try/except Exception` (logs with `exc_info=True`, continues):
  1. Renew held leases via `renew_queue_leases(sorted(owned_queues), self.worker_id, lease_secs)` when `owned_queues` is non-empty.
  2. Compute `unowned = set(self.queues) - owned_queues`; re-claim via `claim_queue_leases(...)`, merge `newly_claimed` into `owned_queues`, log acquisitions (ELECT-06 failover, ELECT-05 daemon authority).
  3. Fire cron for held queues only: `scheduler.run_due_tasks(queue_names=owned_queues)`, log job count (ELECT-01 + ELECT-02).
- Added release-on-shutdown inside the EXISTING `finally:` block, after `update_worker_heartbeat(status='dead')`: `release_queue_leases(sorted(owned_queues), self.worker_id)` wrapped in try/except-log (ELECT-03).
- `self.backend.claim_job(self.queues, self.worker_id)` left byte-for-byte unchanged — all workers still claim/execute jobs from all queues (ELECT-07).

## Requirements Satisfied
- ELECT-01 — claim/renew per-queue lease every poll cycle for all `self.queues`.
- ELECT-02 — `run_due_tasks` runs only for queues the worker holds the lease for.
- ELECT-03 — held leases released on SIGTERM/SIGINT graceful shutdown (via existing `finally:`).
- ELECT-05 — live daemon/holder keeps its lease (enforced by the lease primitive skipping live foreign leases; no extra probe).
- ELECT-06 — dead leader's lease (TTL `poll_interval*3`) is re-claimed next cycle; failover within one TTL.
- ELECT-07 — job-claim path unchanged; all workers execute jobs from all queues; failed election degrades to "this worker doesn't schedule this cycle".

## Verification
- `python -c "import sqlery.core.worker"` exits 0 (no circular import).
- Task 1 AST assertions pass: top-level Scheduler import, `self.poll_interval * 3`, initial `claim_queue_leases`.
- Task 2 source assertions pass: `renew_queue_leases`, `run_due_tasks(queue_names=owned_queues)`, `release_queue_leases` present; `claim_job(self.queues, self.worker_id)` unchanged and NOT scoped to `owned_queues`.
- `black --check` and `ruff check` on `worker.py`: all reported findings are PRE-EXISTING (single-quote style on untouched lines; unused `json` import at line 6; unused `e` in the original main-loop handler at line 623; `TaskExecutor` in `__all__`). Confirmed via `black --diff` that NO reformat lands on any added line, and the added `except Exception as e:` blocks both use `e`. No NEW violations introduced (out-of-scope per plan).

## Deviations from Plan
None — plan executed exactly as written. The discretionary placement of the per-cycle election step (before `claim_job`, inside the loop body) and the inline-port-vs-`election.py` decision (inline) were exercised as the plan's lower-risk recommended options.

## Threat Surface
No new security-relevant surface. Reuses the Phase 8 per-queue lease primitive with process-derived identity (`worker_id`/`node_id`/`pid` — not user input). T-09-03 (brief two-leader double-fire) and cron-semantics hardening are explicitly deferred to Phase 10 per the plan's threat register. No package installs (no new dependencies).

## Notes for Plan 02
Behavioral tests (held-queue cron fires, foreign-lease blocks firing, job-claim spans all queues, release on shutdown) live in Plan 02's `tests/unit/test_worker.py`. This plan delivered the wiring + source-assertion verification only.

## Self-Check: PASSED
- FOUND: src/sqlery/core/worker.py
- FOUND commit: 6b3450d (Task 1)
- FOUND commit: 85159e1 (Task 2)
