# Phase 17: fastapi-parity - Context

**Gathered:** 2026-06-10 (doc ingest — decisions pre-locked; no discussion phase needed)
**Status:** Ready for planning

<domain>
## Phase Boundary

Standalone/SQLAlchemy partition parity (ingest Phase 6; **PLAN.md Step 11**). Depends on Phase 16. Mirror Phases 15–16 in `fastapi_sqlery/`:

- **`database.py`:** emits partitioned DDL + the partial pending index instead of plain `create_all` for the jobs table — a fresh standalone install is partitioned by default.
- **`config.py`:** gains the partition config keys (`SQLERY_PARTITION_INTERVAL`, `SQLERY_PARTITION_PREMAKE`, `SQLERY_PARTITION_RETENTION`, `SQLERY_PARTITION_ARCHIVE_HOOK`, `PARTITION_MAINTENANCE_INTERVAL_MINUTES`, staging threshold) with the same validation invariants as Django mode.
- **`backend.py` + `async_backend.py`:** route cleanup to `reclaim_drained_partitions` when partitioned (the `backend.py:674` path got the batched fallback in Phase 12); prune id-only write paths with `created_at` filters; staging dual-table surface (Phase 14's R5) mirrored.

**Mapped requirements:** R1–R6 re-verified for the SQLAlchemy backend (no new requirement IDs — this phase proves the existing requirements hold in standalone mode).

</domain>

<decisions>
## Implementation Decisions (LOCKED — do not re-ask, do not re-litigate)

- **D1 — Same fixed defaults** mirrored in `fastapi_sqlery/config.py` (in-memory config + env vars), including validation invariants (retention ≫ threshold; maintenance interval ≤ partition interval).
- **D6 — SQLite unchanged in standalone mode too:** `StaticPool`/SQLite installs keep the batched DELETE path; partitioned DDL only for PG.
- **D8 — Fresh installs partition by default:** no flag; `database.py` decides by vendor.
- **D10 — Phase ordering fixed.**

### Claude's Discretion
- How partitioned DDL coexists with Alembic migrations for existing standalone installs (note the pre-existing `20250101_0002` chain collision in carry-forward — do not entangle; it predates this milestone)
- Reuse strategy for `core/partitioning.py` from the SQLAlchemy session/cursor

</decisions>

## Success Criteria (verbatim from GSD-CONTEXT.md Phase 6)

1. Same lifecycle test as Phase 16 (ingest Phase 5) passes against the FastAPI backend.
2. Fresh install via `database.py` creates a partitioned table by default.

## Verification Anchors (from intel/constraints.md)

- The Phase-16 lifecycle test (claim → run → complete → reclaim on a partitioned table) re-run against the SQLAlchemy backend — sync and async.
- R1–R6 acceptance criteria re-checked under standalone mode (partial index used by claims; batched fallback; reclaim; back-pressure invariant; staging surface; single-partition pruning).
- SQLite × PG divergence matrix covers the standalone backend methods too.

<canonical_refs>
## Canonical References

### Technical spec
- `.planning/intel/ingest-src/PLAN.md` — Step 11
- `.planning/intel/requirements.md` — R1–R6 + "Phase 6 — FastAPI parity" section
- `.planning/intel/constraints.md` — config contract (Django defaults + `fastapi_sqlery/config.py` mirror)

### External reference artifacts (read-only, outside this repo)
- `/Users/gabriel/Documents/GitHub/empty/sqlery-pgque/PLAN.md` — original spec
- `/Users/gabriel/Documents/GitHub/empty/sqlery-pgque/sql/pgwq.sql` — partitioned DDL reference for `database.py`

</canonical_refs>

<code_context>
## Existing Code Insights

- `src/sqlery/fastapi_sqlery/database.py` — engine + `create_all` path to replace for the jobs table on PG
- `src/sqlery/fastapi_sqlery/config.py` — `StandaloneConfig` in-memory dict + env var loading (`SQLERY_*`)
- `src/sqlery/fastapi_sqlery/backend.py` (cleanup at :674, batched since Phase 12) and `async_backend.py`
- `src/sqlery/core/models.py` — SQLModel `QueuedJob` (schema must stay synchronized with the Django model's composite-PK shape)
- v0.23.0 precedent: standalone lease parity was built the same way (SQLModel + migration + backend methods matching Django semantics)

## Execution Conventions (intel/constraints.md)

- Conventional single-line commits `(type): description`, < 50 chars, never mention AI
- When changing existing lines: comment out the wrong line, add the corrected line beneath — never delete/replace outright
- Track regressions in `REGRESSIONS.md`; pure functions preferred; complexity ≤ 10; tests describe behavior

</code_context>

<deferred>
## Deferred Ideas

- LISTEN/NOTIFY wake-up — Phase 18 (optional)
- Fixing the pre-existing Alembic `20250101_0002` chain collision — carry-forward item, separate fix

</deferred>

---

*Phase: 17-fastapi-parity*
*Context gathered: 2026-06-10 (doc ingest)*
