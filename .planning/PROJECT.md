# Sqlery — Stability, Coverage, and Operational Confidence

## What This Is

Sqlery is a database-backed Python task queue library that supports two integration modes (Django ORM and Standalone/SQLAlchemy+FastAPI) and six execution modes (daemon, subprocess, HTTP trigger, Lambda/serverless, async worker, synchronous thread). The current milestone is about making those modes boring to operate: trustworthy CI signal, battle-tested failure handling, and operator-grade docs before expanding the product surface further.

## Core Value

Every execution mode works reliably and is tested in CI across both Django and standalone integrations, on both SQLite and PostgreSQL, with operational guidance that maintainers can trust in production.

## Current State

**Shipped:** v0.21 — Feature-Complete Run Modes (2026-05-15). All 6 execution modes (daemon, subprocess, HTTP trigger, Lambda, async worker, synchronous) are production-ready in both Django and standalone integrations, tested on SQLite and Postgres. Async worker was rebuilt with real async backends plus drain-with-deadline shutdown. Security was hardened with dashboard auth, webhook SSRF defense, CSRF protection, and an opt-in task module allowlist. Archive: `.planning/milestones/v0.21-*`.

**Shipped:** v0.22 — Stability, Coverage, and Operational Confidence (released through v0.22.3). CI/coverage signal restored, failure-path and PostgreSQL concurrency hardening added, and operator readiness improved across the six execution modes.

**Shipped:** v0.23.0 — Worker-Elected Cron Scheduler (2026-06-08). A bare `sqlery-worker` cluster now fires recurring cron with no daemon present by self-electing a per-queue scheduler-leader over a real lease scheme, at true parity across {Django, standalone} × {SQLite, Postgres}. Built the standalone `sqlery_daemon_lease` (SQLModel + migration + atomic claim/renew/release), wired core-shared scheduler election into the worker poll loop (daemon stays authoritative; failover within one TTL), hardened cron to fire exactly-once via an atomic `advance_scheduled_task_if_due` CAS with drift correction and an optional jitter knob, and made the full matrix a first-class CI gate. Archive: `.planning/milestones/v0.23.0-*`.

## Next Milestone

None active. Run `/gsd-new-milestone` to start the next cycle. The strongest candidate (per backlog) is the **drop-in compatibility milestone** (Celery/RQ/scheduler permanent shim surface), which has been deliberately deferred behind the v0.22 maturity pass and v0.23 scheduler-parity work.

## Requirements

### Validated

- ✓ Job enqueueing via `@job` decorator, `.enqueue()`, `.delay()`, `.enqueue_at()` — existing
- ✓ Django ORM backend (`DjangoBackend`) — existing
- ✓ SQLAlchemy/SQLModel standalone backend (`SQLAlchemyBackend`) — existing
- ✓ Daemon mode with worker pool, scheduler, heartbeats, and leases — existing
- ✓ Subprocess execution mode (fork-per-job, memory safe) — existing
- ✓ HTTP trigger middleware (Django, signed internal requests) — existing
- ✓ Lambda/serverless handler with EventBridge integration — existing
- ✓ Job scheduling (cron, interval, one-time) — existing
- ✓ Queue priority, tag concurrency, rate limiting, job dependencies — existing
- ✓ Retry with exponential backoff — existing
- ✓ Optimistic locking (SQLite) / SELECT FOR UPDATE SKIP LOCKED (PostgreSQL) — existing
- ✓ Django admin integration and dashboard — existing
- ✓ FastAPI standalone dashboard and REST API — existing (no auth)
- ✓ CLI tools (Typer-based): sqlery, sqlery-worker, sqlery-web, sqlery-daemon, etc. — existing
- ✓ Alembic migrations for standalone mode — existing
- ✓ RQ and django-tasks-scheduler compatibility layers — existing
- ✓ Database resilience (retry decorator, WAL mode, busy_timeout, connection pooling) — existing
- ✓ Trustworthy CI/coverage signal without the collection-error workaround — v0.22
- ✓ Battle-tested failure handling (crash, retry, timeout, zombie, heartbeat, lease recovery) — v0.22
- ✓ Stronger PostgreSQL concurrency and claim/lease regression coverage — v0.22
- ✓ Operator runbooks and troubleshooting docs for production-facing modes — v0.22
- ✓ A bare `sqlery-worker` cluster fires recurring cron tasks with no daemon present, in both Django and standalone modes — v0.23.0
- ✓ Exactly one worker schedules a given queue at a time via a real per-queue lease in both backends — v0.23.0
- ✓ Scheduler leadership fails over to another worker within one lease TTL when the leader dies — v0.23.0
- ✓ Cron ticks are not double-enqueued during brief leader overlap and do not miss or drift — v0.23.0 (atomic `advance_scheduled_task_if_due` CAS)
- ✓ A running daemon stays authoritative for scheduling; workers defer to it — v0.23.0
- ✓ Feature parity across {Django, standalone} × {SQLite, Postgres} is a first-class, CI-enforced acceptance gate — v0.23.0

### Active

(None — v0.23.0 shipped. Next milestone's requirements will be defined via `/gsd-new-milestone`.)

### Out of Scope

- Mobile app or desktop client — this is a Python library
- Redis/RabbitMQ backends — sqlery is database-backed by design
- Multi-tenancy or SaaS features — library for single-project use
- Graphical monitoring dashboard redesign — existing dashboards get auth, not a rewrite
- New trigger modes beyond the 6 identified — future milestone

## Context

Sqlery originated as a Django-specific task queue (django-sql-jobs) and has been migrating toward a dual-mode architecture where a framework-agnostic core delegates to either a Django or SQLAlchemy backend. The execution-mode milestone is now complete and archived, but the audit and backlog still show confidence gaps: coverage is temporarily pinned to a low floor because of test-collection issues, one CI human-verify item remains open, Lambda fidelity is only smoke-tested, and there are follow-up hardening items in webhook SSRF validation and operator guidance.

This milestone deliberately favors maturity over feature expansion. The compatibility milestone remains strategically important, but it moves behind a trust-building pass so the current system is easier to operate, verify, and evolve.

## Constraints

- **Python version**: 3.10+ minimum (uses `X | None` union syntax)
- **Database**: PostgreSQL (production) or SQLite (dev/lightweight). No other DB engines.
- **Backward compatibility**: Public API (`@job`, `enqueue`, `Queue`) must remain stable
- **Fork safety**: Must handle DB connection lifecycle around `os.fork()` correctly
- **No new dependencies**: Prefer using existing deps (httpx, sqlmodel, asyncio) over adding new ones

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| **Maturity before expansion** (2026-05-15) | After shipping all six execution modes in v0.21, the highest-value next step is to raise confidence in CI, battle-test failure paths, and improve operational guidance before adding permanent new compatibility surface area. | — Locked for v0.22 |
| **Drop-in compatibility is a permanent first-class feature** (2026-05-15) | Users migrating from Celery, RQ, or django-tasks-scheduler should change only their import paths. Compat shims (`sqlery.compat.celery`, `sqlery.compat.rq`, `sqlery.compat.scheduler`) are NOT transitional — they stay forever. Means: every public decorator/queue/job API in those libraries needs a sqlery-backed equivalent. Reverses the "Deprecated since v3.1.0 — will be removed in v3.2.0" notes in `compat/rq.py`. | — Locked, implementation pending |
| **Scheduling = holding the per-queue lease** (2026-06-08) | The `DaemonLease` table is keyed per queue, and scheduling is already per-queue. "Being the scheduler for queue X" is identical to "holding queue X's lease." Reuse the existing `sqlery_daemon_lease` scheme — no new role, no `__scheduler__` reserved key, no second table. Lease gates who *fires cron* for a queue, never who *executes* its jobs (job-claiming path untouched). | — Locked for v0.23 |
| **Build real standalone lease for parity** (2026-06-08) | Standalone/SQLAlchemy inherits the ABC default of `claim_queue_leases` (returns all queues) — a silent fake election with no `DaemonLease` SQLModel. Honest parity requires building it from scratch (SQLModel + Alembic migration + SQLAlchemy methods), matching Django semantics (Postgres `FOR UPDATE`, SQLite optimistic CAS). This milestone is therefore not "no migrations." | — Locked for v0.23 |
| **Daemon stays authoritative; election always-on** (2026-06-08) | When a daemon is running it keeps winning the lease and workers defer (backward compatible). Worker scheduler-eligibility is always-on with no config knob. Reuse `check_interval` for poll cadence; lease TTL = `check_interval × 3` (mirrors daemon). Jitter default off (`scheduler_jitter_seconds = 0`). | ✓ Good — shipped v0.23.0 |
| **Atomic advance is the idempotency token** (2026-06-08) | Folded CRON-01 (atomic enqueue+advance) and CRON-04 (exactly-once under two-leader overlap) into one primitive: `advance_scheduled_task_if_due` does a CAS on the observed `next_run_at` (ScheduledTask has no version column) and enqueues in the same transaction. Only the CAS winner enqueues, so double-fire is impossible regardless of brief leader overlap — correctness no longer depends on perfect single-leadership. | ✓ Good — shipped v0.23.0 |
| **Force-standalone honored in mode detection** (2026-06-08) | `_detect_mode()` now returns `standalone` when `SQLERY_FORCE_STANDALONE=1`, making the three existing call sites (subprocess launchers, parity CI rail) genuinely force standalone even when Django is importable — needed for an honest standalone×Postgres CI parity rail. | ✓ Good — shipped v0.23.0 |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-06-08 — after v0.23.0 Worker-Elected Cron Scheduler milestone (shipped)*
