# Sqlery — Stability, Coverage, and Operational Confidence

## What This Is

Sqlery is a database-backed Python task queue library that supports two integration modes (Django ORM and Standalone/SQLAlchemy+FastAPI) and six execution modes (daemon, subprocess, HTTP trigger, Lambda/serverless, async worker, synchronous thread). The current milestone is about making those modes boring to operate: trustworthy CI signal, battle-tested failure handling, and operator-grade docs before expanding the product surface further.

## Core Value

Every execution mode works reliably and is tested in CI across both Django and standalone integrations, on both SQLite and PostgreSQL, with operational guidance that maintainers can trust in production.

## Current State

**Shipped:** v0.21 — Feature-Complete Run Modes (2026-05-15). All 6 execution modes (daemon, subprocess, HTTP trigger, Lambda, async worker, synchronous) are production-ready in both Django and standalone integrations, tested on SQLite and Postgres. Async worker was rebuilt with real async backends plus drain-with-deadline shutdown. Security was hardened with dashboard auth, webhook SSRF defense, CSRF protection, and an opt-in task module allowlist. Archive: `.planning/milestones/v0.21-*`.

**Shipped:** v0.22 — Stability, Coverage, and Operational Confidence (released through v0.22.3). CI/coverage signal restored, failure-path and PostgreSQL concurrency hardening added, and operator readiness improved across the six execution modes.

**Shipped:** v0.23.0 — Worker-Elected Cron Scheduler (2026-06-08). A bare `sqlery-worker` cluster now fires recurring cron with no daemon present by self-electing a per-queue scheduler-leader over a real lease scheme, at true parity across {Django, standalone} × {SQLite, Postgres}. Built the standalone `sqlery_daemon_lease` (SQLModel + migration + atomic claim/renew/release), wired core-shared scheduler election into the worker poll loop (daemon stays authoritative; failover within one TTL), hardened cron to fire exactly-once via an atomic `advance_scheduled_task_if_due` CAS with drift correction and an optional jitter knob, and made the full matrix a first-class CI gate. Archive: `.planning/milestones/v0.23.0-*`.

## Current Milestone — v0.24.0 partition-bloat-elimination (started 2026-06-10)

**Goal:** Eliminate the two unbounded-bloat failure modes in sqlery's PostgreSQL backend — xmin-pinning VACUUM starvation and full-index bloat — by moving the jobs table to daily time-range partitions dropped wholesale when drained. Bloat becomes bounded by `throughput × retention` regardless of uptime.

**Shape:** 7 phases, GLOBAL numbers 12–18 (ingest Phases 1–7), ordering LOCKED (D10). Phase 15 (schema cutover) is the highest-risk phase and gates everything after it; Phase 18 (LISTEN/NOTIFY) is the only optional/droppable phase. See `.planning/ROADMAP.md` and `.planning/REQUIREMENTS.md` (R1–R11).

**Non-goals:** SQLite behavior changes (keeps the batched DELETE path), MySQL support, job-priority redesign, any API surface redesign beyond what dual-table staging requires, performance work unrelated to bloat.

**Sequencing note:** the drop-in compatibility milestone (previously named the strongest next candidate) is deferred again behind this bloat-elimination work. Its "permanent first-class feature" decision concerns permanence, not sequencing — no contradiction (INGEST-CONFLICTS.md INFO, 2026-06-10).

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

v0.24.0 — partition-bloat-elimination (full text in `.planning/REQUIREMENTS.md`):

- R1 Hot claim index contains only pending rows (partial index) — Phase 12
- R2 Cleanup never issues unbounded DELETEs (batched fallback path) — Phase 12
- R3 Finished-job storage reclaimed by partition DROP, immune to xmin pinning — Phases 13, 16
- R4 A partition holding any queued/running row is never dropped — Phase 13
- R5 Far-future scheduled jobs cannot pin a partition (staging + promotion) — Phase 14
- R6 All hot write paths prune to a single partition (composite-key filters) — Phases 15–17
- R7 Existing installs migrate with documented window, idempotent SQL, rename rollback — Phase 15
- R8 Multi-daemon deployments never race partition DDL or promotion (advisory locks) — Phase 13
- R9 Operators get metrics + alerts; DEFAULT-partition rows > 0 is a standing alert — Phases 13, 16
- R10 SQLite behavior unchanged, verified by a divergence matrix — Phases 12, 16
- R11 Library floor raised to Python 3.13 (`requires-python = ">=3.13"`), CI matrix updated — Phase 12

### Out of Scope

- Mobile app or desktop client — this is a Python library
- Redis/RabbitMQ backends — sqlery is database-backed by design
- Multi-tenancy or SaaS features — library for single-project use
- Graphical monitoring dashboard redesign — existing dashboards get auth, not a rewrite
- New trigger modes beyond the 6 identified — future milestone
- MySQL support; job-priority redesign; bloat-unrelated performance work — v0.24.0 non-goals
- Batch claiming (`limit=1` per worker is the intended design) — explicitly out of scope
- PgQue-style append/snapshot-batch streaming backend — separate product surface, not this milestone

## Context

Sqlery originated as a Django-specific task queue (django-sql-jobs) and has been migrating toward a dual-mode architecture where a framework-agnostic core delegates to either a Django or SQLAlchemy backend. The execution-mode milestone is now complete and archived, but the audit and backlog still show confidence gaps: coverage is temporarily pinned to a low floor because of test-collection issues, one CI human-verify item remains open, Lambda fidelity is only smoke-tested, and there are follow-up hardening items in webhook SSRF validation and operator guidance.

v0.24.0 turns to storage durability under sustained load: the PgQue analysis (`.planning/intel/ingest-src/sqlery-vs-pgque.md`) showed sqlery exposed to all seven degradation mechanisms of the SKIP LOCKED + UPDATE/DELETE queue pattern, with xmin-horizon pinning (P4) the worst case. The milestone borrows PgQue's storage discipline (partition + invariant-checked DROP) while keeping work-queue semantics, `limit=1` claims, and per-job state. Reference artifacts live read-only at `/Users/gabriel/Documents/GitHub/empty/sqlery-pgque/`.

## Constraints

<!-- - **Python version**: 3.10+ minimum (uses `X | None` union syntax) -->
- **Python version**: 3.13+ minimum (`requires-python = ">=3.13"`) — floor raised 2026-06-10 (user decision, INGEST-CONFLICTS.md Resolution Log); ships in v0.24.0 Phase 12 (pyproject bump + CI matrix drops 3.11/3.12)
- **Database**: PostgreSQL (production) or SQLite (dev/lightweight). No other DB engines.
- **Backward compatibility**: Public API (`@job`, `enqueue`, `Queue`) must remain stable
- **Fork safety**: Must handle DB connection lifecycle around `os.fork()` correctly
- **No new dependencies**: Prefer using existing deps (httpx, sqlmodel, asyncio) over adding new ones

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| **Maturity before expansion** (2026-05-15) | After shipping all six execution modes in v0.21, the highest-value next step is to raise confidence in CI, battle-test failure paths, and improve operational guidance before adding permanent new compatibility surface area. | — Locked for v0.22 |
| **Drop-in compatibility is a permanent first-class feature** (2026-05-15) | Users migrating from Celery, RQ, or django-tasks-scheduler should change only their import paths. Compat shims (`sqlery.compat.celery`, `sqlery.compat.rq`, `sqlery.compat.scheduler`) are NOT transitional — they stay forever. Means: every public decorator/queue/job API in those libraries needs a sqlery-backed equivalent. Reverses the "Deprecated since v3.1.0 — will be removed in v3.2.0" notes in `compat/rq.py`. | — Locked, implementation pending (deferred again behind v0.24.0) |
| **Scheduling = holding the per-queue lease** (2026-06-08) | The `DaemonLease` table is keyed per queue, and scheduling is already per-queue. "Being the scheduler for queue X" is identical to "holding queue X's lease." Reuse the existing `sqlery_daemon_lease` scheme — no new role, no `__scheduler__` reserved key, no second table. Lease gates who *fires cron* for a queue, never who *executes* its jobs (job-claiming path untouched). | — Locked for v0.23 |
| **Build real standalone lease for parity** (2026-06-08) | Standalone/SQLAlchemy inherits the ABC default of `claim_queue_leases` (returns all queues) — a silent fake election with no `DaemonLease` SQLModel. Honest parity requires building it from scratch (SQLModel + Alembic migration + SQLAlchemy methods), matching Django semantics (Postgres `FOR UPDATE`, SQLite optimistic CAS). This milestone is therefore not "no migrations." | — Locked for v0.23 |
| **Daemon stays authoritative; election always-on** (2026-06-08) | When a daemon is running it keeps winning the lease and workers defer (backward compatible). Worker scheduler-eligibility is always-on with no config knob. Reuse `check_interval` for poll cadence; lease TTL = `check_interval × 3` (mirrors daemon). Jitter default off (`scheduler_jitter_seconds = 0`). | ✓ Good — shipped v0.23.0 |
| **Atomic advance is the idempotency token** (2026-06-08) | Folded CRON-01 (atomic enqueue+advance) and CRON-04 (exactly-once under two-leader overlap) into one primitive: `advance_scheduled_task_if_due` does a CAS on the observed `next_run_at` (ScheduledTask has no version column) and enqueues in the same transaction. Only the CAS winner enqueues, so double-fire is impossible regardless of brief leader overlap — correctness no longer depends on perfect single-leadership. | ✓ Good — shipped v0.23.0 |
| **Force-standalone honored in mode detection** (2026-06-08) | `_detect_mode()` now returns `standalone` when `SQLERY_FORCE_STANDALONE=1`, making the three existing call sites (subprocess launchers, parity CI rail) genuinely force standalone even when Django is importable — needed for an honest standalone×Postgres CI parity rail. | ✓ Good — shipped v0.23.0 |
| **D1 — Daily RANGE partitioning on created_at, fixed defaults** (2026-06-10) | Partition by RANGE on `created_at`, daily intervals. Defaults: `SQLERY_PARTITION_INTERVAL="1 day"`, `SQLERY_PARTITION_RETENTION="30 days"`, `SQLERY_PARTITION_PREMAKE=7`, `PARTITION_MAINTENANCE_INTERVAL_MINUTES=5`, staging threshold = 1 day, `SQLERY_PG_NOTIFY=False`, `SQLERY_PARTITION_ARCHIVE_HOOK=None`. Source: GSD-CONTEXT.md / PLAN.md. | — LOCKED for v0.24.0 |
| **D2 — Hand-rolled partition maintenance, NOT pg_partman** (2026-06-10) | sqlery is a library and cannot demand a PG extension on user databases; pg_partman drops purely by age and lacks the invariant-checked drop (skip partitions with queued/running rows) — the load-bearing safety property. ~100 lines in `core/partitioning.py`. Source: GSD-CONTEXT.md / PLAN.md. | — LOCKED for v0.24.0 |
| **D3 — Migration 0029 is stop-the-world** (2026-06-10) | Stop workers/daemons, run, restart — no online dual-write cutover. Escape hatch for huge tables: run the SQL manually, then `migrate --fake` that migration. Source: GSD-CONTEXT.md / PLAN.md Step 8. | — LOCKED for v0.24.0 |
| **D4 — FK referential integrity to jobs is dropped** (2026-06-10) | `JobRegistry.job` and `Worker.current_job` FKs demoted to plain indexed `BigIntegerField`; orphans on partition drop are an accepted, documented trade-off (`parent_job_id` already a plain int). Source: GSD-CONTEXT.md / PLAN.md Step 7. | — LOCKED for v0.24.0 |
| **D5 — Failed-job history beyond retention destroyed by default** (2026-06-10) | Partition drop deletes failed jobs alongside succeeded ones unless `SQLERY_PARTITION_ARCHIVE_HOOK` is configured. Default is destroy; document loudly. Source: GSD-CONTEXT.md / PLAN.md Step 3. | — LOCKED for v0.24.0 |
| **D6 — SQLite keeps the (batched) DELETE path forever** (2026-06-10) | No partitioning emulation for SQLite; the batched DELETE (Phase 12) is the permanent SQLite / non-partitioned-PG path, not a stopgap. Source: GSD-CONTEXT.md / PLAN.md Step 2. | — LOCKED for v0.24.0 |
| **D7 — Verified literals: status `'queued'`; ordering `-priority, created_at`** (2026-06-10) | Status literal `'queued'` (models.py:351); claim ordering `-priority, created_at` (backend.py:870-874). Pending-index trailing column is `created_at`, byte-identical between the 0028 index and the 0029 DDL — required so the partitioned table carries it forward without a name collision. Source: GSD-CONTEXT.md / PLAN.md Step 1. | — LOCKED for v0.24.0 |
| **D8 — Partitioning is default-on for PG; no feature flag** (2026-06-10) | New PG installs partition by default; existing installs partition on migrating. Only LISTEN/NOTIFY is flagged (`SQLERY_PG_NOTIFY`, opt-in). Source: GSD-CONTEXT.md. | — LOCKED for v0.24.0 |
| **D9 — `pg_try_advisory_lock` per maintenance function** (2026-06-10) | Every maintenance function (partition DDL, reclaim, scheduled-job promotion) is wrapped in `pg_try_advisory_lock`; a daemon that loses the lock skips the tick silently. Source: GSD-CONTEXT.md / PLAN.md Steps 3, 5. | — LOCKED for v0.24.0 |
| **D10 — Phase ordering is fixed** (2026-06-10) | Phases 12–18 run in order; Phase 12 always first; Phase 18 (LISTEN/NOTIFY) is the only one that may be deferred or dropped. Source: GSD-CONTEXT.md. | — LOCKED for v0.24.0 |
| **Python floor raised to 3.13** (2026-06-10) | Resolves the ingest WARNING (GSD-CONTEXT "Python 3.13+ syntax" vs the 3.10 floor): user chose to RAISE the floor — `requires-python = ">=3.13"`, CI matrix drops 3.11/3.12, packaging change explicitly in-scope as an early v0.24.0 task (Phase 12, R11). 3.13+ syntax then permitted in new code. Source: INGEST-CONFLICTS.md Resolution Log. | — LOCKED for v0.24.0 |

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
*Last updated: 2026-06-10 — v0.24.0 partition-bloat-elimination milestone created from doc ingest (Phases 12–18, R1–R11, 10 locked decisions + Python-floor raise)*
