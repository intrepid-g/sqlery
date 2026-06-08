# Phase 9: Core-Shared Scheduler Election - Context

**Gathered:** 2026-06-08
**Status:** Ready for planning
**Mode:** Auto-generated (decisions fully locked by PROJECT.md + REQUIREMENTS; remaining choices are implementation-level / Claude's discretion)

<domain>
## Phase Boundary

Make a bare `sqlery-worker` self-elect as scheduler-leader by participating in the existing per-queue lease scheme (built in Phase 8). Each poll cycle the worker claims/renews the lease for every queue in its configured set and runs due cron only for the queues it holds, via `scheduler.run_due_tasks(queue_names=held)`. A running daemon stays authoritative (keeps winning its leases; workers defer). On graceful shutdown the worker releases held leases so leadership fails over within one lease TTL.

Scope: election + cron-firing wiring in the worker poll loop only, reusing the per-queue lease primitives and `scheduler.run_due_tasks` — no reserved key, no new table. Cron-semantics hardening (atomic advance, drift, jitter, idempotency under overlap) is Phase 10; parity-gated CI tests are Phase 11. Job-claiming/execution throughput is untouched — the lease gates only who *fires cron*, never who *executes* jobs.
</domain>

<decisions>
## Implementation Decisions

### Election Scheme (locked — PROJECT.md Key Decisions, 2026-06-08)
- Scheduling == holding the per-queue `sqlery_daemon_lease`. Reuse the existing scheme; no reserved `__scheduler__` key, no second table.
- Worker scheduler-eligibility is always-on — no config knob (the `WORKER_SCHEDULER_ELIGIBLE` opt-out is explicitly deferred).
- A daemon, when present, keeps renewing its leases and therefore keeps winning; workers never steal a live lease. Daemon stays authoritative (backward compatible). The lease primitives enforce this uniformly — no separate "is a daemon running" probe is required.
- The lease gates cron-firing only. All workers continue to claim and execute jobs from all their queues unchanged (`claim_job(self.queues, ...)` path is not modified).

### Cadence & TTL (locked)
- Reuse the worker's existing poll cadence for the election cycle (`self.poll_interval`, from `WORKER_POLL_INTERVAL`, default 5s) — mirrors how the daemon reuses `check_interval`.
- Lease TTL = poll/check interval × 3 (≈30s with defaults), matching the daemon's TTL formula, so a dead leader's lease expires and another worker takes over within one TTL.
- Jitter is NOT introduced here — `scheduler_jitter_seconds` defaults to 0 and is a Phase 10 concern.

### Worker Identity & Lifecycle (from codebase)
- Use the worker's existing identity for the lease holder id: `self.worker_id` (`worker_{node_id}_{pid}`), `self.node_id`, `os.getpid()` — analogous to the daemon's `daemon_id`/`node_id`/pid.
- Claim/renew every queue in `self.queues` each cycle; run `scheduler.run_due_tasks(queue_names=held)` only for queues actually held.
- Release held leases on graceful shutdown (SIGTERM/SIGINT), inside the existing shutdown path, mirroring `daemon.py:510`.

### Claude's Discretion (implementation-level)
- Exact placement of the election step within `WorkerProcess.run` (before/after the job-claim attempt), how held-queue state is tracked across cycles, transient-claim-failure handling (retry next cycle), and whether to extract a small shared helper so daemon and worker share election logic. Honor existing error-handling conventions (catch/log/continue in the loop; never crash the worker).
</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- **Daemon election reference (port this pattern):** `src/sqlery/core/daemon.py` — `claim_queue_leases` (:363), `renew_queue_leases` (:407), re-claim of expired (:413), `scheduler.run_due_tasks(queue_names=owned_queues)` (:433), `release_queue_leases` on shutdown (:510). This is the exact lifecycle the worker must adopt.
- **Lease primitives (Phase 8):** `backend.claim_queue_leases / renew_queue_leases / release_queue_leases` — now real in both Django (`django_sqlery/backend.py`) and standalone (`fastapi_sqlery/backend.py`).
- **Scheduler:** `src/sqlery/core/scheduler.py` `run_due_tasks(queue_names: list[str] | None = None)` (:29) already filters due tasks by queue (:48-49) — call with the held-queue list.
- **Worker:** `src/sqlery/core/worker.py` `WorkerProcess` — `self.queues` (:424, default `['default']`), `self.worker_id`/`self.node_id`/`self.pid` (:436-438), `self.poll_interval` (:441), main loop `while not self.shutdown_requested` (:495), SIGTERM/SIGINT handler setting `self.shutdown_requested` (:459-476), shutdown/cleanup block (~:570-581).

### Established Patterns
- Lazy `get_backend()` / `get_config()` access; framework-agnostic core delegates to the active backend.
- Loop steps wrapped in try/except: log and continue, never crash the worker loop (worker.py:563).
- TTL/cadence read from config with defaults; daemon TTL = interval × 3 is the precedent to mirror.

### Integration Points
- New election logic lives inside `WorkerProcess.run`'s poll loop (`src/sqlery/core/worker.py`); release wired into the shutdown path.
- No backend or model changes expected — Phase 8 already supplies the lease methods/table in both modes.
</code_context>

<specifics>
## Specific Ideas

Mirror the daemon's per-cycle lease lifecycle (`daemon.py:363-510`) inside the worker loop. Use `scheduler.run_due_tasks(queue_names=held)` for held queues only. Keep the firing exactly-once / drift concerns out of scope (Phase 10).
</specifics>

<deferred>
## Deferred Ideas

- Worker takeover of scheduling even when a daemon is up (daemon stays authoritative by default) — deferred.
- `WORKER_SCHEDULER_ELIGIBLE` opt-out knob (election is always-on) — deferred.
- Cron-semantics hardening (atomic enqueue+advance, drift correction, jitter, idempotency under two-leader overlap) — Phase 10.
- Parity-gated failover / no-duplicate / bare-worker CI tests — Phase 11.
</deferred>
