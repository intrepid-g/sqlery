# Roadmap

## Shipped milestones

- **v0.21 — Feature-Complete Run Modes** (2026-03-18 → 2026-05-15) — 4 phases, 25 plans, 43 requirements. All execution modes production-ready across Django and standalone integrations on SQLite and Postgres; async worker rebuilt; security hardened (dashboard auth, webhook SSRF, CSRF, task module allowlist); test/CI infrastructure rebuilt. Archive: [`milestones/v0.21-ROADMAP.md`](milestones/v0.21-ROADMAP.md) · [`milestones/v0.21-REQUIREMENTS.md`](milestones/v0.21-REQUIREMENTS.md) · [`v0.21-MILESTONE-AUDIT.md`](v0.21-MILESTONE-AUDIT.md)
- **v0.22 — Stability, Coverage, and Operational Confidence** (2026-05-15, released through v0.22.3) — 3 phases (Phases 5–7). Restored trustworthy CI/coverage signal without the collection-error workaround or the emergency coverage floor; battle-tested crash/retry/timeout/zombie/heartbeat/lease recovery and PostgreSQL concurrent-claim behavior; delivered operator runbooks and troubleshooting docs for the production-facing execution modes.

## Active milestone

**v0.23.0 — Worker-Elected Cron Scheduler** (started 2026-06-08)

Goal: Let a bare `sqlery-worker` cluster fire recurring cron tasks with no daemon present by self-electing a per-queue scheduler-leader over the existing lease scheme — at true feature parity across both Django and standalone integration modes, on both SQLite and PostgreSQL.

## Phases

- [x] **Phase 8: Standalone Lease Parity** - Build a real standalone `sqlery_daemon_lease` (SQLModel + Alembic migration + atomic SQLAlchemy claim/renew/release) to replace the silent fake election (completed 2026-06-08)
- [ ] **Phase 9: Core-Shared Scheduler Election** - Lift per-queue claim/renew/release-and-schedule orchestration into core and wire it into the worker poll loop, with the daemon staying authoritative
- [ ] **Phase 10: Harden Cron Semantics** - Atomic enqueue + `next_run_at` advance, drift correction from scheduled time, optional jitter knob, and idempotency under leader overlap
- [ ] **Phase 11: Parity-Gated Tests & CI** - Prove failover, no-duplicate firing, drift correctness, and bare-worker E2E across the full `{Django, standalone} × {SQLite, Postgres}` matrix as a first-class acceptance gate

## Phase Details

### Phase 8: Standalone Lease Parity

**Goal**: A real standalone per-queue lease exists and behaves like Django's, so leader election stops being a silent Django-only fake and the standalone daemon runs against genuine leases
**Depends on**: Nothing (first phase of v0.23.0; foundation for all later phases)
**Requirements**: LEASE-01, LEASE-02, LEASE-03, LEASE-04, LEASE-05
**Success Criteria** (what must be TRUE):

  1. A `sqlery_daemon_lease` SQLModel exists in `src/sqlery/core/models.py` mirroring Django's `DaemonLease` fields (`queue_name` PK, `daemon_id`, `node_id`, `pid`, `acquired_at`, `expires_at`) plus a `version` field for SQLite CAS
  2. A date-prefixed Alembic migration creates the standalone `sqlery_daemon_lease` table following repo migration conventions
  3. `SQLAlchemyBackend` implements real `claim_queue_leases` / `renew_queue_leases` / `release_queue_leases`, replacing the inherited fake-election default
  4. Standalone lease claiming is atomic and matches Django semantics — Postgres uses `SELECT FOR UPDATE`, SQLite uses optimistic CAS on the `version` field
  5. The existing standalone daemon runs against the real leases instead of the silent fake election

<!-- Old: **Plans**: TBD -->
**Plans**: 2 plans (2 waves)

- [x] 08-01-PLAN.md — DaemonLease SQLModel + DAEMON_LEASE constant + date-prefixed Alembic migration (LEASE-01, LEASE-02)
- [x] 08-02-PLAN.md — Real SQLAlchemyBackend atomic claim/renew/release + lease lifecycle tests + daemon parity (LEASE-03, LEASE-04, LEASE-05)

**UI hint**: no

### Phase 9: Core-Shared Scheduler Election

**Goal**: A bare worker self-elects as scheduler-leader by participating in the existing per-queue lease scheme, firing cron only for queues it holds, while a running daemon stays authoritative
**Depends on**: Phase 8
**Requirements**: ELECT-01, ELECT-02, ELECT-03, ELECT-04, ELECT-05, ELECT-06, ELECT-07
**Success Criteria** (what must be TRUE):

  1. A bare `sqlery-worker` cluster fires recurring cron tasks with no daemon present, in both Django and standalone modes
  2. Each poll cycle, core orchestration claims/renews the lease for every queue in the worker's configured set using the existing per-queue primitives (no reserved key, no new table), and the worker runs due cron only for queues it holds via `scheduler.run_due_tasks(queue_names=held)`
  3. When a daemon already owns a queue's lease, a worker never wins it — the daemon stays authoritative and workers defer
  4. Scheduler leadership fails over to another worker within one lease TTL (`check_interval × 3`, ≈30s) when the leader dies
  5. The worker releases held leases on graceful shutdown (SIGTERM/SIGINT), and holding a lease gates only who fires cron — all workers still claim and execute jobs from all queues unchanged

**Plans**: TBD
**UI hint**: no

### Phase 10: Harden Cron Semantics

**Goal**: Cron ticks fire exactly once and on schedule even under crashes and brief two-leader overlap, with no double-fire, skip, or drift
**Depends on**: Phase 8 (lease foundation); may run parallel with Phase 9
**Requirements**: CRON-01, CRON-02, CRON-03, CRON-04
**Success Criteria** (what must be TRUE):

  1. Enqueue and `next_run_at` advance happen atomically in one transaction so a crash cannot double-fire or skip a tick — verified on both backends
  2. The next occurrence is computed from the scheduled time, not wall-clock `now`, correcting drift across ticks
  3. An optional `scheduler_jitter_seconds` knob (default `0`) is available to avoid thundering-herd enqueue
  4. The "already queued" idempotency guard holds under brief two-leader overlap so a cron task fires exactly once

**Plans**: TBD
**UI hint**: no

### Phase 11: Parity-Gated Tests & CI

**Goal**: Failover, single-firing, drift correctness, and bare-worker scheduling are proven identical across the full integration/database matrix and enforced as a first-class CI acceptance gate
**Depends on**: Phase 8, Phase 9, Phase 10
**Requirements**: PARITY-01, PARITY-02, PARITY-03, PARITY-04, PARITY-05
**Success Criteria** (what must be TRUE):

  1. A failover test proves that killing the leader causes another worker to schedule within one TTL, across the full matrix
  2. A no-duplicate test proves two simultaneous leaders fire a cron task exactly once
  3. An atomic-advance/drift test verifies `next_run_at` correctness across several ticks
  4. An end-to-end bare-worker test proves cron fires with only `sqlery-worker` processes and no daemon
  5. Every behavioral test asserts identical outcomes across `{Django, standalone} × {SQLite, Postgres}` as a first-class, CI-enforced acceptance gate

**Plans**: TBD
**UI hint**: no

## Progress

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
<!-- Old: | 8. Standalone Lease Parity | 0/TBD | Not started | - | -->
| 8. Standalone Lease Parity | 2/2 | Complete   | 2026-06-08 |
| 9. Core-Shared Scheduler Election | 0/TBD | Not started | - |
| 10. Harden Cron Semantics | 0/TBD | Not started | - |
| 11. Parity-Gated Tests & CI | 0/TBD | Not started | - |

## Lower-priority / [FOLLOWUP] carry-forward

- Compat milestone (Celery/RQ/scheduler permanent drop-in surface) — deliberately deferred behind the v0.22 maturity pass and the v0.23 scheduler-parity work.
- Worker takeover of scheduling even when a daemon is up — deferred (v0.23 default keeps the daemon authoritative).
- A `WORKER_SCHEDULER_ELIGIBLE` opt-out config knob — deferred (v0.23 default is always-eligible, no knob).
- Lambda fidelity testing (LocalStack/SAM) — deferred from v0.21 Phase 2.
- Dashboard audit logging / rate limiting / payload encryption at rest — future ops/security work.
- Quarterly dead-code retention sweep — each `#CLEANUP` marker has a `Remove after YYYY-MM-DD`; arrive at the date, decide per-file.

See `.planning/BACKLOG.md` for the full backlog.
