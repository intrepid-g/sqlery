# Roadmap

## Shipped milestones

- **v0.21 — Feature-Complete Run Modes** (2026-03-18 → 2026-05-15) — 4 phases, 25 plans, 43 requirements. All execution modes production-ready across Django and standalone integrations on SQLite and Postgres; async worker rebuilt; security hardened (dashboard auth, webhook SSRF, CSRF, task module allowlist); test/CI infrastructure rebuilt. Archive: [`milestones/v0.21-ROADMAP.md`](milestones/v0.21-ROADMAP.md) · [`milestones/v0.21-REQUIREMENTS.md`](milestones/v0.21-REQUIREMENTS.md) · [`v0.21-MILESTONE-AUDIT.md`](v0.21-MILESTONE-AUDIT.md)
- **v0.22 — Stability, Coverage, and Operational Confidence** (2026-05-15, released through v0.22.3) — 3 phases (Phases 5–7). Restored trustworthy CI/coverage signal without the collection-error workaround or the emergency coverage floor; battle-tested crash/retry/timeout/zombie/heartbeat/lease recovery and PostgreSQL concurrent-claim behavior; delivered operator runbooks and troubleshooting docs for the production-facing execution modes.
- **v0.23.0 — Worker-Elected Cron Scheduler** (shipped 2026-06-08) — 4 phases (Phases 8–11), 11 plans, 21 requirements. A bare `sqlery-worker` cluster now fires recurring cron with no daemon present by self-electing a per-queue scheduler-leader over a real lease scheme at true parity across {Django, standalone} × {SQLite, Postgres}: built the standalone `sqlery_daemon_lease` (SQLModel + migration + atomic claim/renew/release), wired core-shared scheduler election into the worker poll loop (daemon stays authoritative, failover within one TTL), hardened cron to fire exactly-once via an atomic `advance_scheduled_task_if_due` CAS with drift correction and a jitter knob, and enforced the full matrix as a first-class CI gate. Archive: [`milestones/v0.23.0-ROADMAP.md`](milestones/v0.23.0-ROADMAP.md) · [`milestones/v0.23.0-REQUIREMENTS.md`](milestones/v0.23.0-REQUIREMENTS.md) · [`milestones/v0.23.0-MILESTONE-AUDIT.md`](milestones/v0.23.0-MILESTONE-AUDIT.md)

## Active milestone — v0.24.0 partition-bloat-elimination

**Started:** 2026-06-10 (created from doc ingest: `.planning/intel/SYNTHESIS.md`; sources `GSD-CONTEXT.md` + `PLAN.md` rev 2026-06-10)

**Goal:** Eliminate the two unbounded-bloat failure modes in sqlery's PostgreSQL backend — xmin-pinning VACUUM starvation and full-index bloat — by moving the jobs table to daily time-range partitions dropped wholesale when drained. Bloat becomes bounded by `throughput × retention` regardless of uptime.

**Done means:** all phase success criteria pass; the PLAN.md Step 13 test matrix is green under both SQLite and PG; a fresh PG install partitions by default; an upgraded install survives the Step 8 migration round-trip on a production-sized snapshot, with documented rollback. Phase 18 (LISTEN/NOTIFY) is optional and may be deferred or dropped without affecting "done".

**Non-goals:** SQLite behavior changes (keeps the batched DELETE path), MySQL support, job-priority redesign, API surface redesign beyond what dual-table staging requires, performance work unrelated to bloat.

**Numbering:** GLOBAL Phases 12–18 (continuing from v0.23.0's Phase 11), mapping 1:1 to the ingest's Phases 1–7. Ordering is LOCKED (decision D10). PLAN.md Step 13 is not a phase — its tests/rollback/metrics are embedded in each phase's success criteria.

## Phases

- [ ] **Phase 12: quick-wins** - Partial pending index + batched DELETE cleanup in both backends, plus the Python 3.13 floor raise; independently shippable
- [ ] **Phase 13: partition-core** - Hand-rolled partition maintenance (`core/partitioning.py`), cleanup routing, daemon tick with advisory locks and the DEFAULT-partition alert
- [ ] **Phase 14: scheduled-job-staging** - `ScheduledJob` staging table + exactly-once promotion so far-future jobs never pin a partition
- [ ] **Phase 15: schema-cutover** - Composite PK + FK demotion + stop-the-world migration 0029 with rename-based rollback (highest risk; gates everything after)
- [ ] **Phase 16: backend-wiring-pruning** - Route Django cleanup/vacuum to partition reclaim and prune all 11 id-only write paths to a single partition
- [ ] **Phase 17: fastapi-parity** - Mirror the partition stack in `fastapi_sqlery/` (DDL, config keys, sync + async backends); re-verify R1–R6 for SQLAlchemy
- [ ] **Phase 18: listen-notify** - Optional opt-in `SQLERY_PG_NOTIFY` sub-100 ms dispatch; may be deferred or dropped

## Phase Details

### Phase 12: quick-wins
**Goal**: The hot claim path scans only pending rows, cleanup never bursts unbounded DELETEs, and the library floor is Python 3.13 — shippable immediately, independent of everything else
**Depends on**: Nothing (first phase; always ships first per D10)
**Requirements**: R1, R2, R11 (R10 partially: SQLite path untouched)
**PLAN.md steps**: 1–2, plus the Python-floor raise (INGEST-CONFLICTS.md Resolution Log)
**Success Criteria** (what must be TRUE):
  1. New index used by the claim query (EXPLAIN shows it); old index gone
  2. Cleanup of a 100k-row backlog never holds a lock > 1 s and never deletes a row claimed mid-loop (test exists)
  3. SQLite path untouched
  4. `requires-python = ">=3.13"` in pyproject.toml; CI matrix updated accordingly (3.11/3.12 dropped); PROJECT.md constraint updated
**Plans**: 3 plans
Plans:
- [ ] 12-01-PLAN.md — Partial pending index: update models.py Meta.indexes + migration 0028 with concurrent ops
- [ ] 12-02-PLAN.md — Batched DELETE cleanup in both backends + behavioral tests
- [ ] 12-03-PLAN.md — Python 3.13 floor: pyproject.toml, CI matrix, PROJECT.md

### Phase 13: partition-core
**Goal**: Partition maintenance machinery exists and is safe — future partitions provisioned ahead, drained partitions reclaimed by DROP under the back-pressure invariant, all DDL coordinated across daemons
**Depends on**: Nothing technically (pure new code + daemon wiring; activates only when the table is partitioned) — ordered after Phase 12 per D10
**Requirements**: R3, R4, R8, R9 (DEFAULT-partition alert)
**PLAN.md steps**: 3–5
**Success Criteria** (what must be TRUE):
  1. Unit tests prove the four reclaim skip-rules including the back-pressure invariant
  2. Two concurrent daemons cause zero DDL errors
  3. DEFAULT-partition row count is exposed and alerts > 0
  4. Reference behavior matches `sql/pgwq.sql`
**Plans**: TBD

### Phase 14: scheduled-job-staging
**Goal**: Far-future scheduled jobs live in a staging table and are promoted exactly-once, so no queued row can pin an otherwise-drained partition
**Depends on**: Phase 13 (daemon tick hosts the promotion loop)
**Requirements**: R5
**PLAN.md steps**: 6
**Success Criteria** (what must be TRUE):
  1. A job scheduled 60 days out is invisible to claims, visible to status/cancel APIs, promoted within one daemon tick of `scheduled_at - lookahead`
  2. Two daemons never double-promote (test with concurrent promoters)
  3. Config validation rejects retention ≤ threshold
**Plans**: TBD

### Phase 15: schema-cutover
**Goal**: The jobs table is partitioned — composite PK `(created_at, id)`, FKs demoted, and existing installs migrate through idempotent stop-the-world migration 0029 with a rename-based rollback
**Depends on**: Phases 13–14. HIGHEST-RISK phase; its verification gates everything after it
**Requirements**: R6 (schema half), R7
**PLAN.md steps**: 7–8
**Success Criteria** (what must be TRUE):
  1. Migration round-trip (legacy → partitioned → rollback) passes on a generated production-sized snapshot (≥1M rows)
  2. Zero rows in DEFAULT after migration
  3. Identity continues from max(id)+1
  4. Re-running the migration after an injected mid-migration failure completes cleanly
  5. The `.pk` audit has zero unaddressed hits
**Plans**: TBD

### Phase 16: backend-wiring-pruning
**Goal**: The Django backend actually uses the partition machinery — cleanup routes to reclaim, vacuum skips the partitioned table, and every hot write path prunes to a single partition
**Depends on**: Phase 15
**Requirements**: R3, R6 (R9 metrics complete; R10 divergence matrix green)
**PLAN.md steps**: 9–10
**Success Criteria** (what must be TRUE):
  1. EXPLAIN on each of the 11 checklist write paths shows single-partition pruning
  2. Full claim → run → complete → reclaim lifecycle test passes on a partitioned table
  3. SQLite divergence matrix green
**Plans**: TBD

### Phase 17: fastapi-parity
**Goal**: The standalone/SQLAlchemy mode has full partition parity — fresh installs partition by default, config keys mirrored, sync + async backends route cleanup and prune writes
**Depends on**: Phase 16
**Requirements**: R1–R6 re-verified for the SQLAlchemy backend (no new requirement IDs)
**PLAN.md steps**: 11
**Success Criteria** (what must be TRUE):
  1. Same lifecycle test as Phase 16 (ingest Phase 5) passes against the FastAPI backend
  2. Fresh install via `database.py` creates a partitioned table by default
**Plans**: TBD

### Phase 18: listen-notify
**Goal**: Opt-in sub-100 ms worker dispatch via PG LISTEN/NOTIFY, byte-identical behavior when the flag is off — OPTIONAL: may be deferred or dropped without affecting milestone "done" (D10)
**Depends on**: Phase 17
**Requirements**: None (documented exception — optional latency improvement, no requirement)
**PLAN.md steps**: 12
**Success Criteria** (what must be TRUE):
  1. With flag on, dispatch latency < 100 ms in test
  2. With flag off (default), behavior is byte-identical to before
**Plans**: TBD

## Progress

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 12. quick-wins | 0/3 | Not started | - |
| 13. partition-core | 0/TBD | Not started | - |
| 14. scheduled-job-staging | 0/TBD | Not started | - |
| 15. schema-cutover | 0/TBD | Not started | - |
| 16. backend-wiring-pruning | 0/TBD | Not started | - |
| 17. fastapi-parity | 0/TBD | Not started | - |
| 18. listen-notify (optional) | 0/TBD | Not started | - |

## Lower-priority / [FOLLOWUP] carry-forward

- Compat milestone (Celery/RQ/scheduler permanent drop-in surface) — deliberately deferred behind the v0.22 maturity pass and the v0.23 scheduler-parity work.
- Worker takeover of scheduling even when a daemon is up — deferred (v0.23 default keeps the daemon authoritative).
- A `WORKER_SCHEDULER_ELIGIBLE` opt-out config knob — deferred (v0.23 default is always-eligible, no knob).
- Clean-DB `alembic upgrade head` collision at `20250101_0002` (`sqlery_worker already exists`) — pre-existing, predates v0.23; the new `20260608_0015` lease migration is correct in isolation. Needs a dedicated migration-chain fix.
- Legacy `scheduler_tasks.py` `claim_due_scheduled_task` non-atomic path was not hardened in v0.23 (runtime path is `sqlery.core.scheduler.Scheduler`) — confirm dead or migrate.
- Lambda fidelity testing (LocalStack/SAM) — deferred from v0.21 Phase 2.
- Dashboard audit logging / rate limiting / payload encryption at rest — future ops/security work.
- Quarterly dead-code retention sweep — each `#CLEANUP` marker has a `Remove after YYYY-MM-DD`; arrive at the date, decide per-file.

See `.planning/BACKLOG.md` for the full backlog.
