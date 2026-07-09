# Roadmap

## Shipped milestones

- **v0.21 — Feature-Complete Run Modes** (2026-03-18 → 2026-05-15) — 4 phases, 25 plans, 43 requirements. All execution modes production-ready across Django and standalone integrations on SQLite and Postgres; async worker rebuilt; security hardened (dashboard auth, webhook SSRF, CSRF, task module allowlist); test/CI infrastructure rebuilt. Archive: [`milestones/v0.21-ROADMAP.md`](milestones/v0.21-ROADMAP.md) · [`milestones/v0.21-REQUIREMENTS.md`](milestones/v0.21-REQUIREMENTS.md) · [`v0.21-MILESTONE-AUDIT.md`](v0.21-MILESTONE-AUDIT.md)

## Active milestone

**v0.22 — Stability, Coverage, and Operational Confidence** (started 2026-05-15)

Goal: increase trust in the six shipped execution modes before adding more complexity by fixing CI/coverage signal, battle-testing failure and concurrency behavior, and improving operator readiness.

## Phases

- [ ] **Phase 5: CI Signal and Coverage Recovery** - Remove temporary test/coverage workarounds and make default plus PostgreSQL CI rails trustworthy
- [ ] **Phase 6: Failure-Path and PostgreSQL Hardening** - Battle-test crash/recovery, retry/timeout, zombie/heartbeat/lease, and concurrent-claim behavior
- [ ] **Phase 7: Operational Readiness** - Improve runbooks/troubleshooting and close or explicitly defer the highest-value remaining operator-facing hardening gaps

## Phase Details

### Phase 5: CI Signal and Coverage Recovery
**Goal**: CI tells the truth about the current system without relying on temporary collection or coverage escape hatches
**Depends on**: Nothing (first phase of v0.22)
**Requirements**: TEST-01, TEST-02, TEST-03, TEST-04
**Success Criteria** (what must be TRUE):
  1. The default non-PostgreSQL suite collects and runs without the known Django collection-error workaround
  2. The PostgreSQL rail collects and runs cleanly in CI with representative mode coverage
  3. `pyproject.toml` coverage settings no longer rely on the documented emergency 13% floor workaround
  4. The standalone-no-Django guarantee is continuously demonstrated in CI or an equivalent automated proof
**Plans**: 3 plans
Plans:
- [ ] 05-01-PLAN.md — Fix collection errors and restore clean default-suite execution
- [ ] 05-02-PLAN.md — Raise coverage gate to a trustworthy clean-suite baseline and keep PG rail green
- [ ] 05-03-PLAN.md — Resolve standalone-no-Django proof gap in CI
**UI hint**: no

### Phase 6: Failure-Path and PostgreSQL Hardening
**Goal**: Production-critical failure and concurrency paths are regression-tested, deterministic, and trusted under realistic worker behavior
**Depends on**: Phase 5
**Requirements**: HARD-01, HARD-02, HARD-03, HARD-04, PG-01, PG-02
**Success Criteria** (what must be TRUE):
  1. Crash/recovery paths for daemon and workers are covered by deterministic regression tests with clear expected terminal states
  2. Retry, timeout, and backoff behavior is validated across representative execution modes
  3. Zombie detection, stale-heartbeat handling, and lease recovery are covered by explicit state-transition tests
  4. PostgreSQL concurrent-claim and contention scenarios are tested under multi-worker pressure
  5. Fork/DB lifecycle handling is validated so subprocess and daemon workers do not retain invalid connection state after fork
**Plans**: 3 plans
Plans:
- [ ] 06-01-PLAN.md — Crash/retry/timeout recovery matrix for core execution modes
- [ ] 06-02-PLAN.md — Zombie, heartbeat, and lease hardening pass
- [ ] 06-03-PLAN.md — PostgreSQL concurrency and fork/DB lifecycle battle tests
**UI hint**: no

### Phase 7: Operational Readiness
**Goal**: Maintainers have production-grade guidance for operating Sqlery and the highest-value remaining trust gaps are resolved or consciously deferred
**Depends on**: Phase 6
**Requirements**: OPS-01, OPS-02, OPS-03
**Success Criteria** (what must be TRUE):
  1. Operators can deploy, run, observe, restart, and recover the core production modes using project docs alone
  2. Troubleshooting docs cover the most likely field failures around heartbeats, stuck jobs, crashes, and DB connectivity
  3. The remaining trust-affecting follow-ups are either implemented in this phase or explicitly deferred with rationale in planning/docs
**Plans**: 2 plans
Plans:
- [ ] 07-01-PLAN.md — Operator runbooks and recovery/troubleshooting docs
- [ ] 07-02-PLAN.md — Close or document trust-affecting follow-up hardening items
**UI hint**: no

## Lower-priority / [FOLLOWUP] carry-forward

- Compat milestone (Celery/RQ/scheduler permanent drop-in surface) — deliberately deferred behind v0.22 maturity pass.
- Lambda fidelity testing (LocalStack/SAM) — deferred from v0.21 Phase 2 unless Phase 7 pulls it forward.
- Dashboard audit logging / rate limiting / payload encryption at rest — future ops/security work.
- Quarterly dead-code retention sweep — each `#CLEANUP` marker has a `Remove after YYYY-MM-DD`; arrive at the date, decide per-file.

See `.planning/BACKLOG.md` for the full backlog.
