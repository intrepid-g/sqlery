# Decisions — partition-bloat-elimination milestone

Ten locked decisions ingested from the milestone context. All are marked **LOCKED** by the
source ("do not re-ask; do not re-litigate"). Source doc is SPEC (precedence 0), not an
Accepted ADR, so classifier `locked=false` — but downstream planners MUST treat these as
settled and not re-open them in discuss phases.

---

## D1 — Daily RANGE partitioning on created_at, with fixed defaults [LOCKED]

Partition by RANGE on `created_at`, daily intervals. Defaults:
`SQLERY_PARTITION_INTERVAL="1 day"`, `SQLERY_PARTITION_RETENTION="30 days"`,
`SQLERY_PARTITION_PREMAKE=7`, `PARTITION_MAINTENANCE_INTERVAL_MINUTES=5`,
staging threshold = 1 day, `SQLERY_PG_NOTIFY=False`, `SQLERY_PARTITION_ARCHIVE_HOOK=None`.

- source: .planning/intel/ingest-src/GSD-CONTEXT.md (Locked decisions #1)
- source: .planning/intel/ingest-src/PLAN.md (Config keys table)
- note: supersedes the hourly/"2 hours" defaults in sqlery-vs-pgque.md Appendix B.7 (DOC, lower precedence)

## D2 — Hand-rolled partition maintenance, NOT pg_partman [LOCKED]

sqlery is a library and cannot demand a PG extension on user databases; pg_partman drops
purely by age and lacks the invariant-checked drop (skip partitions with queued/running
rows), which is the load-bearing safety property. Hand-roll ~100 lines in
`core/partitioning.py`.

- source: .planning/intel/ingest-src/GSD-CONTEXT.md (Locked decisions #2)
- source: .planning/intel/ingest-src/PLAN.md ("Why hand-rolled instead of pg_partman")

## D3 — Step 8 is a stop-the-world migration [LOCKED]

Migration `0029_partition_queued_job` requires a maintenance window (stop workers/daemons,
run, restart). No online dual-write cutover. Escape hatch for huge tables: run the SQL
manually, then `migrate --fake` that specific migration.

- source: .planning/intel/ingest-src/GSD-CONTEXT.md (Locked decisions #3)
- source: .planning/intel/ingest-src/PLAN.md (Step 8; senior review finding #1)

## D4 — FK referential integrity to jobs is dropped [LOCKED]

`JobRegistry.job` and `Worker.current_job` FKs are demoted to plain indexed
`BigIntegerField`. Orphans on partition drop are an accepted, documented trade-off.
(`parent_job_id` is already a plain int.)

- source: .planning/intel/ingest-src/GSD-CONTEXT.md (Locked decisions #4)
- source: .planning/intel/ingest-src/PLAN.md (Step 7; senior review finding #7)

## D5 — Failed-job history beyond retention is destroyed by default [LOCKED]

Partition drop deletes failed jobs alongside succeeded ones unless the operator configures
`SQLERY_PARTITION_ARCHIVE_HOOK`. Default is destroy; document loudly.

- source: .planning/intel/ingest-src/GSD-CONTEXT.md (Locked decisions #5)
- source: .planning/intel/ingest-src/PLAN.md (Step 3 reclaim spec; senior review finding #9)

## D6 — SQLite keeps the (batched) DELETE path forever [LOCKED]

No partitioning emulation for SQLite. The batched DELETE (Phase 1) is the permanent
SQLite / non-partitioned-PG path, not a stopgap.

- source: .planning/intel/ingest-src/GSD-CONTEXT.md (Locked decisions #6)
- source: .planning/intel/ingest-src/PLAN.md (Step 2)

## D7 — Verified literals: status 'queued'; ordering -priority, created_at [LOCKED]

Status literal is `'queued'` (models.py:351); claim ordering is `-priority, created_at`
(backend.py:870-874). The pending index trailing column is `created_at`, byte-identical
between the Phase 1 index migration and the Phase 4 (Step 8) DDL — required so the
partitioned table carries it forward without a name collision.

- source: .planning/intel/ingest-src/GSD-CONTEXT.md (Locked decisions #7)
- source: .planning/intel/ingest-src/PLAN.md (Step 1; senior review findings #3, #12)
- note: supersedes the `(priority DESC, scheduled_at)` index sketch in sqlery-vs-pgque.md Appendix A (DOC)

## D8 — Partitioning is default-on for PG; no feature flag [LOCKED]

New PG installs partition by default; existing installs partition on migrating. Only
LISTEN/NOTIFY is flagged (`SQLERY_PG_NOTIFY`, opt-in).

- source: .planning/intel/ingest-src/GSD-CONTEXT.md (Locked decisions #8)
- note: supersedes the `SQLERY_PG_PARTITIONED=False` opt-in flag in sqlery-vs-pgque.md Appendix B.7 (DOC)

## D9 — Concurrency control via pg_try_advisory_lock per maintenance function [LOCKED]

Every maintenance function (partition DDL, reclaim, scheduled-job promotion) is wrapped in
`pg_try_advisory_lock`; a daemon that loses the lock skips the tick silently.

- source: .planning/intel/ingest-src/GSD-CONTEXT.md (Locked decisions #9)
- source: .planning/intel/ingest-src/PLAN.md (Steps 3, 5; senior review finding #8)

## D10 — Phase ordering is fixed [LOCKED]

Phases run in the order defined in the milestone context. Phase 1 always first; Phase 7
(LISTEN/NOTIFY) is the only one that may be deferred or dropped. All others are fixed.

- source: .planning/intel/ingest-src/GSD-CONTEXT.md (Locked decisions #10)
