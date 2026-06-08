# Phase 9: Core-Shared Scheduler Election - Pattern Map

**Mapped:** 2026-06-08
**Files analyzed:** 1 modified (`src/sqlery/core/worker.py`) + 1 optional helper extraction
**Analogs found:** 1 / 1 (exact lifecycle analog in `daemon.py`)

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `src/sqlery/core/worker.py` (`WorkerProcess.run`) | worker | event-driven / poll-loop | `src/sqlery/core/daemon.py` (`DaemonManager` run loop, lease lifecycle) | exact (lifecycle), role-match (host loop) |
| `src/sqlery/core/election.py` (optional shared helper) | utility | transform | `daemon.py:362-419` claim/renew/re-claim block | exact (extracted from analog) |

The port target is the per-queue lease lifecycle. The host loop it lands in is `WorkerProcess.run`; the lifecycle being copied is the daemon's. Both files are required reading for the planner.

## Pattern Assignments

### `src/sqlery/core/worker.py` — `WorkerProcess.run` (worker, poll-loop)

**Analog:** `src/sqlery/core/daemon.py` lease lifecycle (`:358-419`, `:433`, `:508-512`)

The worker already has every identity/cadence primitive the daemon uses. Map daemon → worker 1:1:

| Daemon (analog) | Worker (target) | Source |
|-----------------|-----------------|--------|
| `daemon_id = f"daemon_{self.node_id}_{os.getpid()}"` (daemon.py:358) | `self.worker_id` = `f"worker_{self.node_id}_{self.pid}"` (already set) | worker.py:438 |
| `self.node_id` | `self.node_id` (already set) | worker.py:436 |
| `os.getpid()` | `self.pid` (already set) | worker.py:437 |
| `check_interval = get_config("DAEMON_CHECK_INTERVAL", 10)` | `self.poll_interval` (already set, `WORKER_POLL_INTERVAL`, default 5) | worker.py:441 |
| `lease_secs = check_interval * 3` | `lease_secs = self.poll_interval * 3` (new local) | daemon.py:360 |
| `queues = get_config("WORKER_QUEUES", ...)` | `self.queues` (already set) | worker.py:424 |
| `scheduler = Scheduler(backend=backend)` | new local in `run()` | daemon.py:354 |

**Imports pattern** — `worker.py` already imports `os`, `signal`, `time`, `get_backend`, `get_config`. The only missing symbol is `Scheduler`. Add a top-level import (project rule: no inline imports):
```python
from .scheduler import Scheduler
```
Note: `worker.py:18` already does `from .utils import import_task`, so `.scheduler` is a sibling top-level import — consistent. (Watch for circular import; `scheduler.py:6` imports only `from ..compat import get_backend` and `..crontab`, so importing it into `worker.py` is safe.)

**Initial claim (before the loop)** — analog `daemon.py:362-364`:
```python
owned_queues = set(
    backend.claim_queue_leases(queues, daemon_id, self.node_id, os.getpid(), lease_secs)
)
```
Worker equivalent (place after `self._heartbeat('idle')` at worker.py:492, before `while not self.shutdown_requested`):
```python
scheduler = Scheduler(backend=self.backend)
lease_secs = self.poll_interval * 3
owned_queues = set(
    self.backend.claim_queue_leases(
        self.queues, self.worker_id, self.node_id, self.pid, lease_secs
    )
)
```
ABC signature confirmed: `claim_queue_leases(queues, daemon_id, node_id, pid, lease_secs) -> list[str]` (compat/__init__.py:118-141). The `daemon_id` parameter is just the holder id — pass `self.worker_id`.

**Per-cycle renew + re-claim expired** — analog `daemon.py:404-419`:
```python
if owned_queues:
    backend.renew_queue_leases(sorted(owned_queues), daemon_id, lease_secs)
unowned = set(queues) - owned_queues
if unowned:
    newly_claimed = set(
        backend.claim_queue_leases(
            sorted(unowned), daemon_id, self.node_id, os.getpid(), lease_secs
        )
    )
    if newly_claimed:
        owned_queues |= newly_claimed
        logger.info(f"Acquired scheduler leases for: {sorted(newly_claimed)}")
```
Worker placement (Claude's discretion per CONTEXT): inside `while not self.shutdown_requested` (worker.py:495), inside its own try/except so a lease error logs-and-continues and never crashes the loop — matching the loop's existing convention (worker.py:562-567). ELECT-05 (daemon stays authoritative) is enforced *by the lease primitive itself* — `claim_queue_leases` skips live leases — so no extra "is a daemon up" probe is needed; this is stated in CONTEXT decisions and visible in the ABC docstring (compat/__init__.py:128-129).

**Fire cron for held queues only** — analog `daemon.py:433`:
```python
jobs = scheduler.run_due_tasks(queue_names=owned_queues)
```
`run_due_tasks(self, queue_names: list[str] | None = None)` (scheduler.py:29) filters due tasks by `t.queue_name in queue_names` (scheduler.py:48-49). Passing a `set` works (`in` membership). Wrap in try/except log-and-continue exactly like daemon.py:432-437. This satisfies ELECT-01 (claim/renew each cycle) and ELECT-02 (fire only held queues).

**Job claiming is untouched (ELECT-07)** — `self.backend.claim_job(self.queues, self.worker_id)` at worker.py:505 must NOT change. The lease gates only the new `run_due_tasks` call; all workers keep claiming/executing jobs from all `self.queues`. Do not scope `claim_job` to `owned_queues`.

**Release on shutdown (ELECT-03)** — analog `daemon.py:508-512`:
```python
try:
    backend.release_queue_leases(sorted(owned_queues), daemon_id)
except Exception as e:
    logger.error(f"Error releasing queue leases: {e}")
```
Worker placement: the `finally:` block at worker.py:571-581 (after `update_worker_heartbeat(status='dead')`). `owned_queues` must be in scope of the `finally` — initialize it before the `try:` at worker.py:494 (i.e. alongside the initial-claim block), not inside the loop, so a crash before first claim still finds a defined (possibly empty) set. SIGTERM/SIGINT already set `self.shutdown_requested` (worker.py:460-476) which exits the loop into `finally` — no new signal wiring needed for release.

### `src/sqlery/core/election.py` (optional shared helper — Claude's discretion)

CONTEXT explicitly leaves "whether to extract a small shared helper so daemon and worker share election logic" to discretion. If extracted, the helper should encapsulate the renew + re-claim-expired block (daemon.py:404-419) as a pure-ish function taking `(backend, queues, owned_queues, holder_id, node_id, pid, lease_secs) -> set[str]` and returning the updated owned set. Both `daemon.py` and `worker.py` would call it. Keep it framework-agnostic (only `backend` + stdlib), matching `scheduler.py`/`claiming.py` conventions. If not extracted, the worker simply inlines the daemon's block (above). Either is acceptable; the inline port is lower-risk for this phase.

## Shared Patterns

### Loop-step error isolation (catch/log/continue, never crash the worker)
**Source:** `worker.py:562-567` (loop-level) and `daemon.py:432-437` / `:508-512` (per-step)
**Apply to:** every new election step (claim, renew, re-claim, `run_due_tasks`, release)
```python
try:
    jobs = scheduler.run_due_tasks(queue_names=owned_queues)
    if jobs:
        logger.info(f"Scheduler created {len(jobs)} jobs")
except Exception as e:
    logger.error(f"Scheduler error: {e}", exc_info=True)
```

### TTL / cadence formula
**Source:** `daemon.py:360` — `lease_secs = check_interval * 3`
**Apply to:** worker — `lease_secs = self.poll_interval * 3` (≈30s default). Satisfies ELECT-06 (failover within one TTL). Jitter stays out (CONTEXT: Phase 10).

### Holder identity
**Source:** worker already constructs `self.worker_id`/`self.node_id`/`self.pid` (worker.py:436-438), structurally identical to `daemon.py:358`'s `daemon_id`. Reuse as-is; do not invent a new id.

### Lazy backend access
**Source:** `WorkerProcess.__init__` already resolves `self.backend = backend or get_backend()` (worker.py:419-423). Use `self.backend`, not a fresh `get_backend()`, in the election code.

## No Analog Found

None. The lease lifecycle has an exact analog in `daemon.py`; the host loop has a role-match analog (the worker's own existing loop structure). All backend lease methods exist (Phase 8): `claim_queue_leases`/`renew_queue_leases`/`release_queue_leases` are real in both `django_sqlery/backend.py` and `fastapi_sqlery/backend.py`, with the ABC contract in `compat/__init__.py:118-169`.

## Metadata

**Analog search scope:** `src/sqlery/core/` (daemon, worker, scheduler), `src/sqlery/compat/`
**Files scanned:** 4 (daemon.py, worker.py, scheduler.py, compat/__init__.py)
**Pattern extraction date:** 2026-06-08
