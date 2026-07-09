# Milestones

## v0.23.0 Worker-Elected Cron Scheduler (Shipped: 2026-06-08)

**Phases completed:** 4 phases, 11 plans, 15 tasks

**Key accomplishments:**

- DaemonLease SQLModel (sqlery_daemon_lease) mirroring Django's lease fields plus a SQLite-CAS version column, a DAEMON_LEASE table constant, and a head-chained Alembic migration that creates the table with an expires_at index.
- SQLAlchemyBackend now implements real per-queue lease claiming (`claim_queue_leases` / `renew_queue_leases` / `release_queue_leases`) with Postgres `FOR UPDATE SKIP LOCKED` and SQLite version-CAS take-over, replacing the inherited ABC fake-election default, and a SQLite test suite proves the full lease lifecycle at parity with the Django/FakeBackend contract.
- 1. [Rule 1 - Bug] Assert election via claim records + cron firing instead of post-cycle `_leases` state
- 1. [Rule 3 - Blocking] `black --check` cannot pass on either file — pre-existing, out of scope
- 1. [Rule 3 - Blocking] `black --check src/sqlery/core/scheduler.py` fails on pre-existing single-quote style — out of scope
- 1. [Rule 1 - Bug] Tests must drive `core.scheduler.Scheduler`, not the legacy `sqlery.executor.TaskExecutor`
- Adds the four missing Postgres cron cells — Django x PG and standalone x PG, each asserting single-fire (PARITY-02) and drift-free next_run_at advance (PARITY-03) against the real hardened Scheduler / advance_scheduled_task_if_due CAS, closing the matrix gap Phase 10 deferred.
- Cross-matrix PARITY-01 (failover) and PARITY-04 (bare-worker E2E) proofs: SQLite in-process election cells plus real-backend Postgres lease-takeover cells for both the Django/active and standalone paths, with no production source changes.
- Closes the silently-skipped standalone x Postgres parity cell by adding a CI step that forces the standalone backend (SQLERY_FORCE_STANDALONE=1) and runs the parity files under -m postgres against postgres:15, with an empty-collection pre-check and no escape hatch — making PARITY-05 a first-class build gate where all four matrix cells actually execute.

---
