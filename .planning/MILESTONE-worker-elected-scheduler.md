# Milestone Plan: Worker-Elected Cron Scheduler

## Summary

Today, recurring cron tasks (`ScheduledTask`) only fire if a **daemon** is running — the
daemon holds a queue lease and exclusively runs `scheduler.run_due_tasks()`. In **bare-worker
deployments** (`sqlery-worker` with no daemon), nobody watches the clock, so cron tasks
silently never fire. One-shot `enqueue_at` jobs are unaffected (they self-serve via the
`scheduled_at` claim filter).

This milestone makes a plain worker **self-elect as scheduler-leader** by participating in the
**same per-queue lease scheme the daemon already uses** — no new table, no reserved key. The
leader for a queue keeps executing jobs *and* fires that queue's cron ticks; on its death,
another worker takes over within one lease TTL. We harden the three cron edge cases flagged in
discovery (idempotent enqueue, atomic `next_run_at` advance, drift correction) **and**,
critically, deliver this at **feature parity across both integration modes** — not Django-only.

## Design: there is no separate "scheduler role"

The `DaemonLease` table is keyed **per queue** (`queue_name` is the PK). Scheduling is already
per-queue: the daemon runs `scheduler.run_due_tasks(queue_names=owned_queues)`
(`daemon.py:431-437`) and `ScheduledTask` carries a `queue_name`. So **"being the scheduler for
queue X" is identical to "holding the lease for queue X."** A worker doesn't need a new role or
a `__scheduler__` row — it just participates in the existing per-queue lease scheme:

- Each worker tries to claim the lease for **all queues in its config** (the queues it already
  serves).
- For every queue it wins, it fires that queue's due cron tasks each cycle.
- One lease-holder per queue ⇒ exactly one scheduler per queue. Multiple queues spread
  naturally across workers, or one worker holds several — both free.
- **Lease = scheduling only.** Holding queue X's lease gates *who fires X's cron*, NOT who
  executes X's jobs. All workers still execute jobs from all queues via the normal
  `FOR UPDATE SKIP LOCKED` / CAS claim. This exactly matches today's daemon semantics — full
  execution throughput is preserved, and the job-claiming path is untouched.

Consequences: **no second table, no database pollution, no reserved-key special-casing, total
reuse** of `claim/renew/release_queue_leases`. Worker and daemon become the same election
participant. (Standalone still must *build* the `sqlery_daemon_lease` table because it doesn't
exist there yet — that is the parity work — but it is the *same* table Django has.)

## Parity correction (the reason for this revision)

The first draft assumed the lease was reusable infrastructure in both modes. **It is not.**

- The `DatabaseBackend` ABC *declares* `claim_queue_leases` / `renew_queue_leases` /
  `release_queue_leases` (`compat/__init__.py:118,143,158`), but the non-abstract default of
  `claim_queue_leases` just returns `list(queues)`.
- **Django** implements them for real (`django_sqlery/backend.py:896-990`) backed by a
  `DaemonLease` model (`django_sqlery/models.py:1191`).
- **Standalone / SQLAlchemy does NOT override them.** It inherits the ABC default — a
  **silent fake election** where every worker "wins" all queues. There is **no `DaemonLease`
  SQLModel** at all (`fastapi_sqlery/async_backend.py:15` comments on this exact gap).

So leader election is **functionally Django-only today.** Honest feature parity requires
**building the standalone lease from scratch** (SQLModel + Alembic migration + SQLAlchemy
backend methods). This milestone is therefore **no longer "no migrations."**

## Background (verified during discovery)

- **Daemon scheduling already works in Django.** `daemon.py:431-437` runs
  `scheduler.run_due_tasks()` only for `owned_queues`; ownership is via `DaemonLease`.
- **Bare workers do NOT schedule (either mode).** `sqlery-worker` only claims and executes
  jobs; it never runs the scheduler loop.
- **`enqueue_at` needs no leader (both modes, confirmed).** Future-dated jobs are written
  straight to the queue with a future `scheduled_at`; the claim query already filters
  `scheduled_at IS NULL OR scheduled_at <= now`:
  - Django: `src/sqlery/django_sqlery/backend.py:805`
  - SQLAlchemy: `src/sqlery/fastapi_sqlery/backend.py:179-182`
  So this milestone is purely about **recurring cron `ScheduledTask`s**.

## Objectives

1. A bare `sqlery-worker` cluster fires cron tasks with no daemon present — **in both Django
   and standalone modes**.
2. Exactly one worker schedules at a time (single-leader via a real lease in both backends).
3. Automatic failover within one TTL (≈30s) when the leader dies — both modes.
4. No double-enqueue during brief leader overlap; no missed/drifting ticks.
5. Backward compatible: when a daemon *is* running, it still wins/owns scheduling — workers
   stay deferential.
6. **Feature parity is a first-class acceptance gate**, not an afterthought.

## Decisions locked in discovery

| Decision | Choice |
|---|---|
| Topology | Bare workers self-elect a clock-watcher |
| Role overlap | Leader keeps executing jobs *and* schedules |
| Parity depth | **Full parity** — build the standalone lease (SQLModel + Alembic migration + SQLAlchemy methods) |
| Code home | **Core orchestration + thin backend lease primitives** — election loop lives in core; each backend only implements atomic claim/renew/release |
| Atomicity | **Match Django** — Postgres `FOR UPDATE` row-lock; SQLite optimistic/CAS (version field), same pattern as `QueuedJob` |
| Election model | **Per-queue lease reuse** — worker claims leases for all configured queues; lease-holder schedules that queue. No reserved key, no new table. (`__scheduler__` idea dropped.) |
| Lease meaning | **Scheduling only** — gates who fires cron for a queue, NOT who executes its jobs. Job-claiming path untouched. |
| Poll/TTL | Reuse `check_interval` (≈10s); lease TTL = 3× |
| Scope | `ScheduledTask` cron only (`enqueue_at` already handled) |
| Cron semantics | Idempotent enqueue + atomic `next_run_at` advance + drift correction w/ jitter |

## Proposed phases & waves

### Phase 1 — Standalone lease parity (the new foundation)
- **Wave 1a:** Add a lease SQLModel to `src/sqlery/core/models.py` mirroring Django
  `DaemonLease` (fields: `queue_name` PK, `daemon_id`, `node_id`, `pid`, `acquired_at`,
  `expires_at`, plus a `version` field for SQLite CAS). Table name parity:
  `sqlery_daemon_lease`.
- **Wave 1b:** Alembic migration for the new table (date-prefixed, per repo convention).
- **Wave 1c:** Implement `claim_queue_leases` / `renew_queue_leases` / `release_queue_leases`
  in `SQLAlchemyBackend` (`fastapi_sqlery/backend.py`), matching Django semantics —
  `FOR UPDATE` on Postgres, optimistic CAS on SQLite.
- **Exit gate:** the existing standalone daemon (which already *calls* these and would crash
  today) now runs with real leases. This phase removes the silent fake election.

### Phase 2 — Core-shared scheduler election in the worker loop
- **Wave 2a:** Lift the "for each configured queue, try-claim-or-renew its lease; for every
  queue I hold, run its due cron tasks; release on shutdown" orchestration into **core**
  (`worker.py` / `scheduler.py`), calling only backend lease primitives. Reuses the exact
  per-queue lease the daemon uses — no reserved key, no special-casing.
- **Wave 2b:** Wire into the worker poll loop (`worker.py` / `worker_runner.py`): each cycle,
  claim/renew leases for the worker's configured queues; for held queues call
  `scheduler.run_due_tasks(queue_names=held)`; release on graceful shutdown (SIGTERM/SIGINT).
  Identical code path drives both modes. Job execution is unaffected — all workers still claim
  and run jobs from all queues as before.
- **Coexistence:** if a daemon already owns a queue's lease, the worker never wins it — no
  conflict.

### Phase 3 — Harden cron semantics (can run parallel with Phase 2)
- **Wave 3a:** Make enqueue + `next_run_at` advance **atomic** in one transaction (tighten
  `scheduler.py:66-120`) so a crash can't double-fire or skip — verified on both backends.
- **Wave 3b:** Drift correction — compute next occurrence from the *scheduled* time, not
  wall-clock `now`; add small optional jitter knob to avoid herd.
- **Wave 3c:** Confirm/strengthen the existing "already queued" idempotency guard
  (`scheduler.py:66`) holds under two-leader overlap.

### Phase 4 — Tests & CI proof (parity-gated)
Every test runs the **full matrix: {Django, standalone} × {SQLite, Postgres}**.
- Failover: kill leader → another worker schedules within one TTL.
- No-dupe under overlap: two simultaneous leaders → cron fires once.
- Atomic-advance/drift: verify `next_run_at` across several ticks.
- E2E bare-worker: only `sqlery-worker` processes, cron task fires.
- **Parity assertion:** the same behavioral test asserts identical outcomes in both modes.

## Can advance now (unattended)

- **No reserved key, no new table.** Reuse the existing per-queue `sqlery_daemon_lease`
  (built fresh on the standalone side as parity work). Settled.
- **Atomicity strategy** = match Django (Postgres row-lock / SQLite CAS) — same pattern
  already proven for `QueuedJob.version`.
- **Jitter default** off (`scheduler_jitter_seconds = 0`) as the live line, with the
  alternative (small default like `5`) commented one-line beside it per repo convention.
- **Scheduler poll cadence** = existing `check_interval`; TTL = `check_interval * 3` (mirrors
  daemon).
- All test scaffolding for the four done-criteria across the full parity matrix.

## Needs your decision

1. **~~Lease table: shared or separate.~~** RESOLVED — reuse the existing per-queue
   `sqlery_daemon_lease`; no second table, no reserved key. The `__scheduler__` row idea was
   dropped once we recognized scheduling is already per-queue.
2. **Daemon vs. worker precedence when both exist.** Default: daemon keeps winning (claims the
   lease continuously), workers defer. Confirm, or allow worker takeover even when a daemon is
   up.
3. **Config knob exposure.** Should "is this worker eligible to become scheduler?" be a setting
   (`WORKER_SCHEDULER_ELIGIBLE=True` default) in both `DJANGO_SQL_JOBS` and `StandaloneConfig`,
   or always-on with no knob? Default: always-eligible, no knob.

## Open questions / assumptions

- Assuming the standalone daemon is currently latent/unused (since its lease calls would crash
  today); Phase 1 makes it functional as a side benefit. Verify no callers depend on the
  current fake-election behavior.
- Assuming `check_interval` is accessible in bare-worker config in both modes; if a worker-only
  deployment lacks it, default it to the daemon's value.
- Assuming Alembic is the migration path for standalone (per `alembic.ini`); Phase 1b follows
  the date-prefixed naming convention.

## Key code anchors

| Component | File | Line |
|---|---|---|
| Lease methods declared (ABC) | `src/sqlery/compat/__init__.py` | 118, 143, 158 |
| Lease impl (Django, real) | `src/sqlery/django_sqlery/backend.py` | 896-990 |
| Lease impl (SQLAlchemy) | `src/sqlery/fastapi_sqlery/backend.py` | **MISSING — to build** |
| DaemonLease model (Django) | `src/sqlery/django_sqlery/models.py` | 1191-1214 |
| Lease SQLModel (standalone) | `src/sqlery/core/models.py` | **MISSING — to build** |
| Standalone lease gap note | `src/sqlery/fastapi_sqlery/async_backend.py` | 15 |
| Daemon scheduler invocation | `src/sqlery/core/daemon.py` | 363, 407, 413, 431-437, 510 |
| Scheduler due-task + enqueue | `src/sqlery/core/scheduler.py` | 29, 66-120 |
| Next-run calculation | `src/sqlery/core/scheduler.py` / `src/sqlery/crontab.py` | 130 / 133 |
| `scheduled_at` claim filter (Django) | `src/sqlery/django_sqlery/backend.py` | 805 |
| `scheduled_at` claim filter (SQLAlchemy) | `src/sqlery/fastapi_sqlery/backend.py` | 179-182 |
| ScheduledTask model | `src/sqlery/core/models.py` | 19-56 |

---

*Scope: 3 implementation phases + 1 parity-gated test phase. **Includes a new standalone lease
table + Alembic migration** (corrected from the first draft). Election orchestration lives in
core; backends supply only atomic lease primitives, keeping Django and FastAPI at true feature
parity.*
