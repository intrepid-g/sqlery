# Requirements: Sqlery v0.22 — Stability, Coverage, and Operational Confidence

**Defined:** 2026-05-15
**Core Value:** Every execution mode works reliably and is tested in CI across both Django and standalone integrations, on both SQLite and PostgreSQL, with operational guidance that maintainers can trust in production.

## v1 Requirements

### Testing and CI

- [ ] **TEST-01**: The default non-PostgreSQL test suite collects and runs without the known Django collection-error workaround that kept coverage pinned to a temporary 13% floor
- [ ] **TEST-02**: The PostgreSQL test rail runs cleanly in CI with representative multi-mode coverage and without collection-time failures
- [ ] **TEST-03**: Coverage configuration no longer depends on the documented 13%-floor workaround and enforces a materially higher gate backed by a clean collected suite
- [ ] **TEST-04**: The standalone-no-Django confidence check is continuously verified in CI, either by satisfying the pending human-verify item or by replacing it with equivalent automated proof

### Failure Handling and Recovery

- [ ] **HARD-01**: Daemon and worker crash-recovery paths are covered by deterministic regression tests that verify jobs recover or fail terminally as designed
- [ ] **HARD-02**: Retry, timeout, and backoff behavior is covered by regression tests across representative execution modes
- [ ] **HARD-03**: Zombie detection, stale-heartbeat handling, and lease-recovery behavior are covered by regression tests that validate the intended state transitions
- [ ] **HARD-04**: Fork and database-connection lifecycle behavior is validated for subprocess and daemon-driven execution so workers do not reuse invalid DB state after fork

### PostgreSQL Concurrency

- [ ] **PG-01**: PostgreSQL claiming behavior is regression-tested under concurrent workers so lock/lease semantics are validated beyond single-worker happy paths
- [ ] **PG-02**: PostgreSQL queue fairness and dependency/rate-limit edge cases have representative coverage under realistic worker contention

### Operational Readiness

- [ ] **OPS-01**: Operator docs explain how to deploy, run, observe, restart, and recover the daemon, worker, and web-facing modes in production-oriented environments
- [ ] **OPS-02**: Troubleshooting docs cover the most likely field failures for heartbeats, stuck jobs, crash recovery, and database connectivity
- [ ] **OPS-03**: The highest-value documented hardening follow-up items that still affect production trust are either closed in this milestone or explicitly deferred with rationale in project docs

## v2 Requirements

### Compatibility Expansion

- **COMP-01**: Add `sqlery.compat.celery` for representative import-path-only migration
- **COMP-02**: De-deprecate and broaden `sqlery.compat.rq`
- **COMP-03**: Audit and broaden `sqlery.compat.scheduler`
- **COMP-04**: Add compat contract tests and migration docs for all supported shims

### Further Hardening

- **OPS-04**: LocalStack / SAM fidelity testing for Lambda mode
- **OPS-05**: Audit logging for dashboard actions
- **OPS-06**: Dashboard rate limiting
- **OPS-07**: Payload encryption at rest
- **OPS-08**: Quarterly dead-code retention sweep execution

## Out of Scope

| Feature | Reason |
|---------|--------|
| New compatibility shims or broad compat-surface expansion | Expansion can wait until the current six modes are more deeply battle-tested |
| Full parity with Celery, RQ, or django-tasks-scheduler | Large permanent support surface; lower leverage than maturity work right now |
| New trigger modes beyond the six already shipped | Not required to raise confidence in the current system |
| Dashboard feature expansion (beyond essential operator guidance) | This milestone is about trust and operations, not UI growth |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| TEST-01 | Phase 5 | Pending |
| TEST-02 | Phase 5 | Pending |
| TEST-03 | Phase 5 | Pending |
| TEST-04 | Phase 5 | Pending |
| HARD-01 | Phase 6 | Pending |
| HARD-02 | Phase 6 | Pending |
| HARD-03 | Phase 6 | Pending |
| HARD-04 | Phase 6 | Pending |
| PG-01 | Phase 6 | Pending |
| PG-02 | Phase 6 | Pending |
| OPS-01 | Phase 7 | Pending |
| OPS-02 | Phase 7 | Pending |
| OPS-03 | Phase 7 | Pending |

**Coverage:**
- v1 requirements: 13 total
- Mapped to phases: 13
- Unmapped: 0

---
*Requirements defined: 2026-05-15*
*Last updated: 2026-05-15 after milestone v0.22 reset*
