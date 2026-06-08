---
phase: 08-standalone-lease-parity
plan: 01
subsystem: database
tags: [sqlmodel, alembic, sqlite, leases, leader-election, migration]

# Dependency graph
requires: []
provides:
  - "DaemonLease SQLModel (table sqlery_daemon_lease) mirroring Django's DaemonLease + version CAS column"
  - "DAEMON_LEASE table-name constant in tables.py"
  - "Alembic migration 20260608_0015 creating sqlery_daemon_lease with expires_at index"
  - "DaemonLease registered on SQLModel.metadata via alembic/env.py"
affects: [08-02-standalone-lease-backend, standalone-leader-election, fastapi-backend]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Cross-mode model mirror: standalone SQLModel field-for-field parity with Django model + SQLite CAS version column"
    - "Date-prefixed Alembic migration chaining from current head with constant-sourced table/index names"

key-files:
  created:
    - "alembic/versions/20260608_0015_add_daemon_lease.py"
  modified:
    - "src/sqlery/core/models.py"
    - "src/sqlery/tables.py"
    - "alembic/env.py"

key-decisions:
  - "Added a version column (default 0) to the standalone DaemonLease for SQLite CAS, matching QueuedJob.version; Django has none because it relies on IntegrityError/filtered-update."
  - "Sourced table/index names from the DAEMON_LEASE constant (no hardcoded literals reaching DDL)."
  - "Kept the version field declaration verbatim-consistent with the existing QueuedJob.version line per plan instruction, rather than black-wrapping it."

patterns-established:
  - "Standalone lease schema parity: SQLModel mirror + Alembic create_table + env.py metadata registration as a single foundation unit."

requirements-completed: [LEASE-01, LEASE-02]

# Metrics
duration: 13min
completed: 2026-06-08
---

# Phase 8 Plan 01: Standalone Lease Schema Foundation Summary

**DaemonLease SQLModel (sqlery_daemon_lease) mirroring Django's lease fields plus a SQLite-CAS version column, a DAEMON_LEASE table constant, and a head-chained Alembic migration that creates the table with an expires_at index.**

## Performance

- **Duration:** ~13 min
- **Started:** 2026-06-08T08:28:00Z
- **Completed:** 2026-06-08T08:41:22Z
- **Tasks:** 2
- **Files modified:** 4 (3 modified, 1 created)

## Accomplishments
- Added `DaemonLease(SQLModel, table=True)` to `core/models.py` with exactly seven columns (`queue_name` PK, `daemon_id`, `node_id`, `pid`, `acquired_at`, `expires_at` indexed, `version` default 0), mirroring Django's `DaemonLease` plus the SQLite-CAS `version` delta.
- Added the `DAEMON_LEASE = "sqlery_daemon_lease"` constant to `tables.py`.
- Registered `DaemonLease` on `SQLModel.metadata` via the `alembic/env.py` model import.
- Created migration `20260608_0015_add_daemon_lease.py` chaining from head `20260514_0014`, creating `sqlery_daemon_lease` with `ix_sqlery_daemon_lease_expires_at` and `version` `server_default='0'`, with a downgrade that drops the index then the table.

## Task Commits

Each task was committed atomically:

1. **Task 1: Add DaemonLease SQLModel and DAEMON_LEASE constant** - `ac6bac0` (feat)
2. **Task 2: Register DaemonLease in alembic env and add create-table migration** - `9155b53` (feat)

## Files Created/Modified
- `src/sqlery/core/models.py` - Added the `DaemonLease` SQLModel (mirrors Django lease + version CAS column).
- `src/sqlery/tables.py` - Added `DAEMON_LEASE` table-name constant.
- `alembic/env.py` - Extended the model import to include `DaemonLease` so `SQLModel.metadata` carries the table.
- `alembic/versions/20260608_0015_add_daemon_lease.py` - New migration creating `sqlery_daemon_lease` with the `expires_at` index.

## Decisions Made
- **version column for standalone only:** Standalone needs optimistic-locking CAS for SQLite (no `SELECT FOR UPDATE`), so a `version` column (default 0) was added mirroring `QueuedJob.version`. Django's `DaemonLease` has none because it uses `IntegrityError`/filtered-update semantics.
- **Constant-sourced DDL names:** Migration imports `DAEMON_LEASE` from `sqlery.tables` and uses literal index name; no user/runtime input reaches DDL (threat T-08-01 mitigated).
- **version line kept consistent with precedent:** The plan instructed copying `QueuedJob.version` verbatim; that existing line is >100 chars and is not black-wrapped in the repo, so the new `DaemonLease.version` line was left consistent with it rather than reformatted.

## Deviations from Plan

None - plan executed exactly as written. No deviation rules (1-4) were triggered; both tasks' code was implemented as specified.

## Issues Encountered

1. **Environment lacked installed dependencies.** The worktree had no `.venv`; the shared project `.venv` (Python 3.12) was missing `alembic` (and `sqlmodel`/standalone extras got dropped mid-session). Resolved by reinstalling the project's *already-declared* dependencies into the project venv via `uv pip install -e ".[standalone]"` (no new dependency introduced — alembic/sqlmodel are existing pyproject deps). Verifications were then run with the worktree `src` on `PYTHONPATH` so they exercised the worktree source, confirmed via `models.__file__`.

2. **Pre-existing migration chain bug (out of scope).** `alembic upgrade head` from a fully empty SQLite DB fails at the long-standing migration `20250101_0002_worker_table.py` (`table sqlery_worker already exists`) because `20250101_0001` also creates that table. This collision predates Phase 08 and also occurs upgrading only to the prior head `20260514_0014`, so it is not caused by the new `20260608_0015` migration. Logged to `deferred-items.md`. The new migration was instead verified in isolation: stamp a fresh DB at `20260514_0014` → `upgrade head` → `downgrade 20260514_0014`. This confirmed a single linear head `20260608_0015`, `down_revision == '20260514_0014'`, the table with the exact 7 columns / `queue_name` PK / `ix_sqlery_daemon_lease_expires_at` index / `version` `server_default='0'`, and that downgrade removes the index then the table. `SQLModel.metadata.create_all` also produces the table.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- The physical `sqlery_daemon_lease` table and `DaemonLease` ORM model exist, unblocking Plan 02 (standalone `claim_queue_leases`/`renew_queue_leases`/`release_queue_leases` against the real table, replacing the ABC fake-election default).
- The `version` column is in place for the SQLite CAS path Plan 02 will implement.
- Note for deploy: the pre-existing `0001`/`0002` `sqlery_worker` duplication should be fixed separately before relying on a clean end-to-end `alembic upgrade head` (see `deferred-items.md`).

## TDD Gate Compliance
N/A - plan type is `execute`, not `tdd`. No RED/GREEN gate required.

## Self-Check: PASSED

All created/modified files exist on disk and both task commits (`ac6bac0`, `9155b53`) are present in git history.

---
*Phase: 08-standalone-lease-parity*
*Completed: 2026-06-08*
