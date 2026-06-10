# Requirements — v0.24.0 partition-bloat-elimination

**Defined:** 2026-06-10 (doc ingest: GSD-CONTEXT.md + PLAN.md rev 2026-06-10)
**Status:** Active — all 11 requirements in scope for v0.24.0

R1–R10 are carried verbatim from `.planning/intel/requirements.md` (synthesized from
`.planning/intel/ingest-src/GSD-CONTEXT.md`; PLAN.md step numbers are the authoritative
technical spec). R11 was added per the user-approved Python-floor resolution
(`.planning/INGEST-CONFLICTS.md` Resolution Log, 2026-06-10). Phase numbers below are
GLOBAL (12–18); the ingest's original Phase 1–7 numbers are noted in parentheses.

---

## REQ-partial-pending-index (R1) — Phase 12 (ingest Phase 1; PLAN Step 1)

Hot claim index contains only pending rows (partial index).

- Description: Replace the full composite index at `django_sqlery/models.py:592` with
  `(queue_name, priority DESC, created_at) WHERE status='queued'`, name
  `sqlery_job_pending_idx`. Migration `0028_partial_pending_index.py`, `atomic=False`,
  `AddIndexConcurrently`/`RemoveIndexConcurrently`. Definition byte-identical to the
  Phase 15 (Step 8) DDL.
- Acceptance: EXPLAIN shows the claim query using the new index; old index gone;
  SQLite path untouched.
- source: .planning/intel/ingest-src/GSD-CONTEXT.md (R1, Phase 1)
- source: .planning/intel/ingest-src/PLAN.md (Step 1)

## REQ-batched-delete-cleanup (R2) — Phase 12 (ingest Phase 1; PLAN Step 2)

Cleanup never issues unbounded DELETEs (batched fallback path).

- Description: Keyset-batched loop (BATCH=500, `order_by("id")`, status re-check inside the
  DELETE, 0.1 s inter-batch sleep) in `django_sqlery/backend.py:455` and
  `fastapi_sqlery/backend.py:674`. Permanent SQLite / non-partitioned-PG path.
- Acceptance: cleanup of a 100k-row backlog never holds a lock > 1 s and never deletes a
  row claimed mid-loop (test exists).
- source: .planning/intel/ingest-src/GSD-CONTEXT.md (R2, Phase 1)
- source: .planning/intel/ingest-src/PLAN.md (Step 2; senior review findings #5, #14)

## REQ-partition-drop-reclaim (R3) — Phases 13, 16 (ingest Phases 2, 5; PLAN Steps 3–5, 9)

Finished-job storage is reclaimed by partition DROP, immune to xmin pinning.

- Description: `core/partitioning.py` with `ensure_future_partitions` (attach-conflict
  handling), `reclaim_drained_partitions` (DETACH → optional archive hook → DROP),
  `check_default_partition`, `_list_partitions`. Raw cursor, no ORM imports.
  `core/cleanup.py` routes via `partitioned_pg=True`; backend `cleanup_jobs` routes to
  reclaim; `vacuum_database` skips the partitioned jobs table.
- Acceptance: full claim → run → complete → reclaim lifecycle test passes on a partitioned
  table; reference behavior matches `sql/pgwq.sql`.
- source: .planning/intel/ingest-src/GSD-CONTEXT.md (R3, Phases 2 and 5)
- source: .planning/intel/ingest-src/PLAN.md (Steps 3–5, 9)

## REQ-backpressure-invariant (R4) — Phase 13 (ingest Phase 2; PLAN Step 3)

A partition holding any queued/running row is never dropped.

- Description: reclaim skip-rules — skip DEFAULT, skip inside retention window, skip any
  partition with queued/running rows.
- Acceptance: unit tests prove all four skip-rules, including the back-pressure invariant.
- source: .planning/intel/ingest-src/GSD-CONTEXT.md (R4, Phase 2)
- source: .planning/intel/ingest-src/PLAN.md (Step 3, Step 13 test matrix)

## REQ-scheduled-staging (R5) — Phase 14 (ingest Phase 3; PLAN Step 6)

Far-future scheduled jobs cannot pin a partition (staging table + promotion).

- Description: `ScheduledJob` model + migration `0030_scheduled_job_staging.py`; enqueue
  routes `scheduled_at > now() + 1 day` to staging; scheduler promotes in one transaction
  (`DELETE … FOR UPDATE SKIP LOCKED RETURNING` → `INSERT`). Dual-table API surface
  (status, fetch-by-id, cancel/delete, list) spans both tables in both adapters; shared id
  sequence. Config validation rejects retention ≤ threshold.
- Acceptance: a job scheduled 60 days out is invisible to claims, visible to status/cancel
  APIs, promoted within one daemon tick of `scheduled_at - lookahead`; concurrent daemons
  never double-promote.
- source: .planning/intel/ingest-src/GSD-CONTEXT.md (R5, Phase 3)
- source: .planning/intel/ingest-src/PLAN.md (Step 6; senior review finding #6)

## REQ-single-partition-writes (R6) — Phases 15 (schema half), 16; Phase 17 for SQLAlchemy (ingest Phases 4–6; PLAN Steps 7, 10, 11)

All hot write paths prune to a single partition (composite-key filters).

- Description: composite PK `("created_at", "id")` (Django 5.2 CompositePrimaryKey);
  `created_at` added to every id-only write path — the 11-item checklist in PLAN.md
  Step 10 (db_compat.py:100,139; models.py:623,656,707,823; backend.py:294,632,637-642,
  772,792,898). No `.only()`/`values()` trims `created_at`.
- Acceptance: EXPLAIN on each checklist write path shows single-partition pruning.
- source: .planning/intel/ingest-src/GSD-CONTEXT.md (R6, Phases 4–6)
- source: .planning/intel/ingest-src/PLAN.md (Steps 7, 10; senior review finding #13)

## REQ-migration-rollback (R7) — Phase 15 (ingest Phase 4; PLAN Step 8)

Existing installs migrate with a documented window, idempotent SQL, and a rename-based rollback.

- Description: `0029_partition_queued_job.py` (`atomic=False`, PG-only): guarded idempotent
  rename, `LIKE … INCLUDING DEFAULTS INCLUDING STORAGE` (NOT IDENTITY),
  `ADD GENERATED BY DEFAULT AS IDENTITY` + `setval`, historical partitions created BEFORE
  the bulk copy, DEFAULT partition, the Phase-12 index verbatim; legacy table dropped in
  a separate later migration so rollback is a rename. Preceded by a `.pk`/FK blast-radius
  audit.
- Acceptance: round-trip (legacy → partitioned → rollback) passes on a ≥1M-row snapshot;
  zero rows in DEFAULT after migration; identity continues from max(id)+1; re-run after an
  injected mid-migration failure completes cleanly; `.pk` audit has zero unaddressed hits.
- source: .planning/intel/ingest-src/GSD-CONTEXT.md (R7, Phase 4)
- source: .planning/intel/ingest-src/PLAN.md (Step 8; senior review findings #1, #2)

## REQ-advisory-lock-coordination (R8) — Phase 13 (ingest Phase 2; PLAN Steps 3, 5)

Multi-daemon deployments never race partition DDL or promotion (advisory locks).

- Description: `pg_try_advisory_lock` per maintenance function; lock loser skips the tick.
  Daemon `PARTITION_MAINTENANCE_INTERVAL_MINUTES` (default 5, must be ≤ partition interval).
- Acceptance: two concurrent daemons cause zero DDL errors; two daemons never double-promote.
- source: .planning/intel/ingest-src/GSD-CONTEXT.md (R8, Phase 2)
- source: .planning/intel/ingest-src/PLAN.md (Steps 3, 5; senior review finding #8)

## REQ-operator-metrics (R9) — Phase 13 (alert), metrics complete by Phase 16 (ingest Phases 2, 5; PLAN Steps 3, 13)

Operators get metrics + alerts; DEFAULT-partition rows > 0 is a standing alert.

- Description: via the existing daemon stats path — partition count, DEFAULT-partition row
  count (alert > 0), oldest undrained partition age, staging-table depth, maintenance-tick
  duration.
- Acceptance: DEFAULT-partition row count exposed and alerting > 0 (Phase 13); all five
  metrics exist by Phase 16.
- source: .planning/intel/ingest-src/GSD-CONTEXT.md (R9, Phase 2; Verification anchors)
- source: .planning/intel/ingest-src/PLAN.md (Step 13 metrics; senior review finding #10)

## REQ-sqlite-unchanged (R10) — Phases 12, 16 (ingest Phases 1, 5; PLAN Steps 2, 13)

SQLite behavior is unchanged and verified by a divergence matrix.

- Description: SQLite keeps the (batched) DELETE path; migration 0029 is a no-op on SQLite;
  divergence matrix runs every backend method under both vendors.
- Acceptance: SQLite path untouched in Phase 12; SQLite × PG divergence matrix green in
  Phase 16.
- source: .planning/intel/ingest-src/GSD-CONTEXT.md (R10, Phases 1 and 5)
- source: .planning/intel/ingest-src/PLAN.md (Steps 2, 8, 13)

## REQ-python-313-floor (R11) — Phase 12

Library floor raised to Python 3.13 (`requires-python = ">=3.13"`), CI matrix updated accordingly.

- Description: bump `requires-python` in pyproject.toml from `">=3.10"` to `">=3.13"`;
  drop 3.11/3.12 from the CI test matrix; update the PROJECT.md constraint. 3.13+ syntax
  is then permitted throughout new code (resolves the GSD-CONTEXT "Python 3.13+ syntax"
  convention vs the old 3.10 floor).
- Acceptance: pyproject.toml carries `requires-python = ">=3.13"`; CI runs (and passes) on
  the updated matrix only; PROJECT.md Constraints reflect 3.13+.
- source: .planning/INGEST-CONFLICTS.md (Resolution Log, 2026-06-10 — user decision: RAISE the floor)

---

## Phase 17 — FastAPI parity (no new R; R1–R6 re-verified for the SQLAlchemy backend)

Mirror Phases 15–16 in `fastapi_sqlery/` (`database.py` partitioned DDL + partial index for
new installs, `config.py` partition keys, sync + async backends). Same lifecycle test as
Phase 16 against the FastAPI backend; fresh install creates a partitioned table by default.

- source: .planning/intel/ingest-src/GSD-CONTEXT.md (Phase 6)
- source: .planning/intel/ingest-src/PLAN.md (Step 11)

## Phase 18 — LISTEN/NOTIFY (optional; no requirement; may be deferred or dropped)

Opt-in `SQLERY_PG_NOTIFY`; `pg_notify` after enqueue; worker LISTEN loop with poll
fallback. With flag on, dispatch latency < 100 ms in test; with flag off (default),
behavior byte-identical to before. Documented exception to phase→requirement coverage.

- source: .planning/intel/ingest-src/GSD-CONTEXT.md (Phase 7)
- source: .planning/intel/ingest-src/PLAN.md (Step 12)

> PLAN.md Step 13 (tests/rollback/metrics) is NOT a phase — its items are distributed into
> each phase's success criteria.

---

## Traceability

| Requirement | ID | Phase(s) | Status |
|-------------|----|----------|--------|
| REQ-partial-pending-index | R1 | Phase 12 (re-verified for SQLAlchemy in Phase 17) | Pending |
| REQ-batched-delete-cleanup | R2 | Phase 12 (re-verified for SQLAlchemy in Phase 17) | Pending |
| REQ-partition-drop-reclaim | R3 | Phases 13, 16 (re-verified for SQLAlchemy in Phase 17) | Pending |
| REQ-backpressure-invariant | R4 | Phase 13 (re-verified for SQLAlchemy in Phase 17) | Pending |
| REQ-scheduled-staging | R5 | Phase 14 (re-verified for SQLAlchemy in Phase 17) | Pending |
| REQ-single-partition-writes | R6 | Phases 15 (schema half), 16; Phase 17 for SQLAlchemy | Pending |
| REQ-migration-rollback | R7 | Phase 15 | Pending |
| REQ-advisory-lock-coordination | R8 | Phase 13 | Pending |
| REQ-operator-metrics | R9 | Phase 13 (alert); metrics complete by Phase 16 | Pending |
| REQ-sqlite-unchanged | R10 | Phases 12, 16 | Pending |
| REQ-python-313-floor | R11 | Phase 12 | Pending |

Coverage: 11/11 requirements mapped to ≥1 phase. Every phase carries ≥1 requirement
except Phase 18 (documented exception: optional latency improvement, no requirement,
droppable without affecting milestone "done").

---
*Last updated: 2026-06-10 — created at v0.24.0 milestone ingest*
