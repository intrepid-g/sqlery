# Phase 13: partition-core - Context

**Gathered:** 2026-06-10 (doc ingest — decisions pre-locked; no discussion phase needed)
**Status:** Ready for planning

<domain>
## Phase Boundary

Partition maintenance machinery (ingest Phase 2; **PLAN.md Steps 3–5**). Pure new code + daemon wiring; activates only when the jobs table is partitioned (which happens in Phase 15). No technical dependency — ordered after Phase 12 per D10.

- **`core/partitioning.py` (new, Step 3):** framework-agnostic, takes a raw DB cursor, NO Django or SQLAlchemy imports. Functions: `ensure_future_partitions` (CREATE IF NOT EXISTS for next N intervals; must catch the attach-conflict error — rows in DEFAULT overlapping a new range — and alert instead of wedging the loop), `reclaim_drained_partitions` (skip DEFAULT, skip inside retention, skip queued/running rows; then DETACH → optional archive hook → DROP), `check_default_partition` (row count, alert > 0), `_list_partitions` (pg_inherits + pg_get_expr).
- **`core/cleanup.py` (edit, Step 4):** route cleanup to partition maintenance when the backend signals `partitioned_pg=True` (backend exposes a `_partitioned_pg()` helper, wired fully in Phase 16).
- **`core/daemon.py` (edit, Step 5):** `PARTITION_MAINTENANCE_INTERVAL_MINUTES` (default 5, must be ≤ partition interval); run ensure + reclaim on this cadence; every maintenance function wrapped in `pg_try_advisory_lock`, skipping the tick if not acquired.

**Mapped requirements:** R3 (REQ-partition-drop-reclaim — core half), R4 (REQ-backpressure-invariant), R8 (REQ-advisory-lock-coordination), R9 (REQ-operator-metrics — DEFAULT-partition alert; full metric set completes by Phase 16).

</domain>

<decisions>
## Implementation Decisions (LOCKED — do not re-ask, do not re-litigate)

- **D1 — Daily RANGE partitioning on `created_at`, fixed defaults:** `SQLERY_PARTITION_INTERVAL="1 day"`, `SQLERY_PARTITION_RETENTION="30 days"`, `SQLERY_PARTITION_PREMAKE=7`, `PARTITION_MAINTENANCE_INTERVAL_MINUTES=5`, `SQLERY_PARTITION_ARCHIVE_HOOK=None`. (The DOC's hourly/"2 hours" sketch is superseded.)
- **D2 — Hand-rolled maintenance, NOT pg_partman:** sqlery cannot demand a PG extension; pg_partman lacks the invariant-checked drop — the load-bearing safety property. ~100 lines in `core/partitioning.py`.
- **D5 — Failed-job history beyond retention is destroyed by default:** partition drop deletes failed jobs alongside succeeded ones unless the operator configures `SQLERY_PARTITION_ARCHIVE_HOOK`. Document loudly.
- **D8 — Partitioning is default-on for PG; no feature flag:** this machinery is not gated; it self-activates when the table is partitioned.
- **D9 — `pg_try_advisory_lock` per maintenance function:** a daemon that loses the lock skips the tick silently. Applies to partition DDL, reclaim, and (Phase 14) promotion.
- **D10 — Phase ordering fixed.**

### Claude's Discretion
- Advisory-lock key derivation scheme
- Internal structure/test harness for raw-cursor unit tests

</decisions>

## Success Criteria (verbatim from GSD-CONTEXT.md Phase 2)

1. Unit tests prove the four reclaim skip-rules including the back-pressure invariant.
2. Two concurrent daemons cause zero DDL errors.
3. DEFAULT-partition row count is exposed and alerts > 0.
4. Reference behavior matches `sql/pgwq.sql`.

## Verification Anchors (from intel/constraints.md)

- Test matrix items owned here: drop-skips-live-partition (back-pressure invariant); DEFAULT-partition alert fires when a row lands there.
- Reclaim order: DETACH PARTITION first (shrinks lock window) → archive hook → DROP.
- `ensure_future_partitions` must catch the attach-conflict error and alert instead of wedging the maintenance loop.
- Config validation invariant: `PARTITION_MAINTENANCE_INTERVAL_MINUTES` ≤ `SQLERY_PARTITION_INTERVAL`.
- Metric started here: DEFAULT-partition row count (alert > 0); remaining four metrics (partition count, oldest undrained partition age, staging-table depth, maintenance-tick duration) must all exist by Phase 16.

<canonical_refs>
## Canonical References

### Technical spec
- `.planning/intel/ingest-src/PLAN.md` — Steps 3–5 (+ "Why hand-rolled instead of pg_partman"; senior findings #2, #8, #9, #11)
- `.planning/intel/requirements.md` — R3, R4, R8, R9
- `.planning/intel/constraints.md` — config contract + validation invariants, operational protocol

### External reference artifacts (read-only, outside this repo)
- `/Users/gabriel/Documents/GitHub/empty/sqlery-pgque/sql/pgwq.sql` — verified pure-SQL reference: partitioned queue with invariant-checked rotation (PG 16). **Reference behavior target for this phase.**
- `/Users/gabriel/Documents/GitHub/empty/sqlery-pgque/PLAN.md` — original spec
- `/Users/gabriel/Documents/GitHub/empty/sqlery-pgque/sqlery-vs-pgque.md` — rationale only (P1–P7 exposure analysis); Appendix literals SUPERSEDED

</canonical_refs>

<code_context>
## Existing Code Insights

- `src/sqlery/core/cleanup.py` — CleanupManager, retention-based cleanup routing point
- `src/sqlery/core/daemon.py` — DaemonManager tick loop; existing `AUTO_CLEANUP_JOBS` gate to sit alongside
- Anti-pattern to honor: no Django models imported at module level in `core/` — partitioning takes a raw cursor only
- Daemon stats path exists for exposing the DEFAULT-partition metric

## Execution Conventions (intel/constraints.md)

- Conventional single-line commits `(type): description`, < 50 chars, never mention AI
- When changing existing lines: comment out the wrong line, add the corrected line beneath — never delete/replace outright
- Track regressions in `REGRESSIONS.md`; pure functions preferred; complexity ≤ 10; tests describe behavior

</code_context>

<deferred>
## Deferred Ideas

- Scheduled-job promotion loop — Phase 14 (hosted by this phase's daemon tick)
- Backend `cleanup_jobs` → reclaim routing and vacuum skip — Phase 16

</deferred>

---

*Phase: 13-partition-core*
*Context gathered: 2026-06-10 (doc ingest)*
