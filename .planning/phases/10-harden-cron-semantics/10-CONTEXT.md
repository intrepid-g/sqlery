# Phase 10: Harden Cron Semantics - Context

**Gathered:** 2026-06-08
**Status:** Ready for planning
**Mode:** Auto-generated (decisions locked by REQUIREMENTS CRON-01..04 + PROJECT.md; atomic mechanism is Claude's discretion / for planning research)

<domain>
## Phase Boundary

Make cron ticks fire **exactly once and on schedule** even under worker/daemon crashes and brief two-leader overlap — no double-fire, no skip, no drift. Four hardening changes to the scheduler (and supporting backend methods) in `src/sqlery/core/scheduler.py` and the backends:

1. **CRON-01 — Atomic enqueue + advance:** Job enqueue and the `next_run_at` advance must happen in one transaction so a crash between them cannot double-fire or skip a tick. Verified on both Django and standalone backends.
2. **CRON-02 — Drift correction:** Compute the next occurrence from the task's *scheduled* time (`task.next_run_at`), not wall-clock `now`, so ticks don't drift later each cycle.
3. **CRON-03 — Jitter knob:** Optional `scheduler_jitter_seconds` config (default `0`) to spread enqueue and avoid thundering-herd.
4. **CRON-04 — Idempotency under overlap:** The "already queued" guard must hold under brief two-leader overlap so exactly one job is created per tick (replace the current check-then-act race with an atomic/CAS or constraint-backed guard).

Scope: scheduler firing correctness + the backend primitives it needs. NOT in scope: election wiring (Phase 9, done) or the cross-matrix parity CI proof (Phase 11). This phase also closes the two overlap/lease residuals carried forward from Phase 9's review (see `CARRY-FORWARD-from-09.md`).
</domain>

<decisions>
## Implementation Decisions

### Locked (REQUIREMENTS + PROJECT.md)
- Next-occurrence base time = the task's scheduled `next_run_at`, not `now` (drift correction). When the schedule has fallen far behind, advance to the next future occurrence (don't replay every missed tick) — keep behavior sane for long downtime.
- `scheduler_jitter_seconds` default `0` (jitter off by default; PROJECT.md locked). When > 0, apply bounded random delay to enqueue/scheduling, not to `next_run_at` computation correctness.
- Atomicity and idempotency must hold identically across {Django, standalone} × {SQLite, Postgres}, mirroring the Phase 8 lease split: Postgres row locking / `SELECT FOR UPDATE`, SQLite optimistic CAS on a version/`next_run_at` predicate.
- The lease gates *who fires* cron (Phase 9); CRON-04 must make double-fire impossible even if two leaders briefly overlap — i.e. correctness cannot depend on perfect single-leadership.

### Claude's Discretion (mechanism — resolve during planning)
- Exact atomic mechanism for CRON-01/CRON-04: e.g. a conditional `update_scheduled_task_next_run` that advances `next_run_at` only `WHERE next_run_at == <observed due time>` (CAS), so only the first leader's advance succeeds and it alone enqueues; or a unique dedup key on `(scheduled_task_id, scheduled_for)` for the created job. Choose the option that is atomic on both backends and reuses existing claim patterns.
- Whether jitter is a `time.sleep` before enqueue vs an offset — keep it from breaking the drift/idempotency guarantees.
- Where `scheduler_jitter_seconds` is read from (config via `get_config`, consistent with existing config access).
</decisions>

<code_context>
## Existing Code Insights

### Current behavior (the gaps to fix)
- `src/sqlery/core/scheduler.py` `_enqueue_for_scheduled_task` (:66): does `has_pending_job_for_scheduled_task` check → `create_job` → separate `update_scheduled_task_next_run`. **Two non-atomic ops** (CRON-01 gap) and a **check-then-act race** (CRON-04 gap).
- `calculate_next_run` (:130): `base_time` defaults to `datetime.now(timezone.utc)` — **wall-clock drift** (CRON-02 gap). Fix: pass `task.next_run_at` as `base_time`. It already accepts a `base_time` param and is tz-safe.
- `run_due_tasks` (:29) iterates `get_due_scheduled_tasks()` and calls `_enqueue_for_scheduled_task` per task, wrapped in try/except (keep this resilience).
- No jitter anywhere (CRON-03 gap).
- Schedule types handled: `cron` (uses `calculate_next_run`), `interval`, `once` (disables task). Preserve all three; the atomic-advance fix must cover at least the `cron` path and not regress interval/once.

### Reusable Assets / Patterns
- Phase 8 atomic-claim split (Postgres `SELECT FOR UPDATE` vs SQLite version-CAS): `src/sqlery/fastapi_sqlery/backend.py` `_claim_one_lease` and `determine_claim_strategy` — the template for an atomic conditional advance.
- `next_cron_occurrence` in `src/sqlery/crontab.py` (used by `calculate_next_run`).
- Backend methods to extend/add: `update_scheduled_task_next_run`, `has_pending_job_for_scheduled_task`, `create_job`, `get_due_scheduled_tasks` — present in the `DatabaseBackend` ABC (`src/sqlery/compat/__init__.py`) and both backends (`django_sqlery/backend.py`, `fastapi_sqlery/backend.py`).
- Config access: `get_config(name, default)` (consistent across core).

### Integration Points
- Scheduler is invoked by both the daemon (`daemon.py:433`) and now the worker (`worker.py`, Phase 9) via `run_due_tasks(queue_names=...)`. The hardened firing path is shared by both callers automatically.
- Any new/changed backend method must be implemented in BOTH backends with matching semantics (parity is the milestone's first-class gate, proven in Phase 11).

### Carry-forward from Phase 9 review
See `.planning/phases/10-harden-cron-semantics/CARRY-FORWARD-from-09.md`: CRON-04 must close the two-leader double-fire (Phase 9 WR-02) and the in-wait lease-renewal-without-expiry-guard overlap window (Phase 9 WR-01 residual).
</code_context>

<specifics>
## Specific Ideas

Prefer an atomic conditional `next_run_at` advance (CAS on the observed due time) so the act of advancing the schedule is itself the idempotency token — the leader that wins the advance is the one that enqueues. This makes double-fire impossible regardless of overlap and folds CRON-01 and CRON-04 into one mechanism. Compute the new `next_run_at` from the old `next_run_at` (CRON-02).
</specifics>

<deferred>
## Deferred Ideas

- Cross-matrix parity CI proof of single-firing / drift / failover — Phase 11 (PARITY-01..05).
- Replaying every missed tick during long downtime (out of scope — advance to next future occurrence instead).
</deferred>
