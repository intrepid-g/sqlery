# GSD Milestone Context — sqlery partition-based bloat elimination

> **Purpose of this file:** complete, self-sufficient context for generating a GSD
> milestone and all phases with zero human intervention. Feed it to
> `/gsd-ingest-docs` or `/gsd-new-milestone` from the target repo. Every decision
> a discuss-phase would ask about is pre-answered in **Locked decisions**. The
> authoritative technical spec is `PLAN.md` in this directory (step numbers below
> refer to it); the analysis behind it is `sqlery-vs-pgque.md`.

---

## Target repo and working setup

- **Code repo (where all work happens):** `/Users/gabriel/Documents/GitHub/sqlery-public`
- **Planning artifacts (read-only references):** `/Users/gabriel/Documents/GitHub/empty/sqlery-pgque/`
  - `PLAN.md` — the reviewed, revised implementation plan (source of truth; includes a senior review, all 14 findings already folded in)
  - `sqlery-vs-pgque.md` — full analysis (PgQue mechanics, sqlery exposure scorecard, partition design appendices)
  - `sql/pgwq.sql` — verified pure-SQL reference for partitioned queue + invariant-checked rotation (PG 16)
  - `clients/python/pgwq/` — reference Python client (LISTEN/NOTIFY worker loop)
- Key source files in sqlery-public: `src/sqlery/django_sqlery/{models.py,backend.py,async_backend.py,db_compat.py}`,
  `src/sqlery/fastapi_sqlery/{backend.py,async_backend.py,database.py,config.py}`,
  `src/sqlery/core/{cleanup.py,daemon.py}`. Latest Django migration is `0027_*`; new ones are 0028/0029/0030.

## Milestone definition

- **Name:** `partition-bloat-elimination`
- **Goal:** Eliminate the two unbounded-bloat failure modes in sqlery's PostgreSQL
  backend — xmin-pinning VACUUM starvation and full-index bloat — by moving the
  jobs table to daily time-range partitions dropped wholesale when drained.
  Bloat becomes bounded by `throughput × retention` regardless of uptime.
- **Non-goals (out of scope):** SQLite behavior changes (keeps the DELETE path),
  MySQL support, job-priority redesign, any API surface redesign beyond what
  dual-table staging requires, performance work unrelated to bloat.
- **Done means:** all phase success criteria below pass; the test matrix in
  PLAN.md Step 13 is green under both SQLite and PG; a fresh PG install partitions
  by default; an upgraded install survives the Step 8 migration round-trip on a
  production-sized snapshot, with documented rollback.

## Requirements (map every phase to these)

- **R1** Hot claim index contains only pending rows (partial index).
- **R2** Cleanup never issues unbounded DELETEs (batched fallback path).
- **R3** Finished-job storage is reclaimed by partition DROP, immune to xmin pinning.
- **R4** A partition holding any queued/running row is never dropped (back-pressure invariant).
- **R5** Far-future scheduled jobs cannot pin a partition (staging table + promotion).
- **R6** All hot write paths prune to a single partition (composite-key filters).
- **R7** Existing installs migrate with a documented window, idempotent SQL, and a rename-based rollback.
- **R8** Multi-daemon deployments never race partition DDL or promotion (advisory locks).
- **R9** Operators get metrics + alerts (DEFAULT-partition rows > 0 is a standing alert).
- **R10** SQLite behavior is unchanged and verified by a divergence matrix.

## Phase breakdown

### Phase 1 — Quick wins (PLAN.md Steps 1–2) — requirements R1, R2
Independently shippable; no dependency on later phases.
- Partial pending index `(queue_name, priority DESC, created_at) WHERE status='queued'`
  replacing the full composite index at `django_sqlery/models.py:592`. Migration
  `0028_partial_pending_index.py`, `atomic=False`, `AddIndexConcurrently`/`RemoveIndexConcurrently`.
- Batched DELETE in `django_sqlery/backend.py:455` and `fastapi_sqlery/backend.py:674`:
  ordered subselect, status re-check inside the DELETE, 0.1 s inter-batch sleep.
- **Success criteria:** new index used by the claim query (EXPLAIN shows it);
  old index gone; cleanup of a 100k-row backlog never holds a lock > 1 s and
  never deletes a row claimed mid-loop (test exists); SQLite path untouched.

### Phase 2 — Partition core (PLAN.md Steps 3–5) — requirements R3, R4, R8, R9
Depends on: nothing (pure new code + daemon wiring; activates only when the table is partitioned).
- New `core/partitioning.py`: `ensure_future_partitions` (handles attach-conflict
  errors), `reclaim_drained_partitions` (skip DEFAULT, skip inside retention,
  skip queued/running, DETACH → optional archive hook → DROP),
  `check_default_partition`, `_list_partitions`. All take a raw cursor; no ORM imports.
- `core/cleanup.py` routes to partition maintenance when backend signals `partitioned_pg=True`.
- `core/daemon.py`: `PARTITION_MAINTENANCE_INTERVAL_MINUTES` (default 5); every
  maintenance function wrapped in `pg_try_advisory_lock`, skipping the tick if not acquired.
- **Success criteria:** unit tests prove the four reclaim skip-rules including the
  back-pressure invariant; two concurrent daemons cause zero DDL errors;
  DEFAULT-partition row count is exposed and alerts > 0; reference behavior matches `sql/pgwq.sql`.

### Phase 3 — Scheduled-job staging (PLAN.md Step 6) — requirement R5
Depends on: Phase 2 (daemon tick hosts the promotion loop).
- `ScheduledJob` model + migration `0030_scheduled_job_staging.py`; enqueue routes
  `scheduled_at > now() + 1 day` to staging; `core/scheduler.py` promotes in one
  transaction: `DELETE … FOR UPDATE SKIP LOCKED RETURNING` → `INSERT`.
- Dual-table API surface: status lookup, fetch-by-id, cancel/delete, list — all
  span both tables in both adapters. Shared id sequence so ids never collide.
- **Success criteria:** a job scheduled 60 days out is invisible to claims, visible
  to status/cancel APIs, promoted within one daemon tick of `scheduled_at - lookahead`;
  two daemons never double-promote (test with concurrent promoters); config
  validation rejects retention ≤ threshold.

### Phase 4 — Schema cutover (PLAN.md Steps 7–8) — requirements R6 (schema half), R7
Depends on: Phases 2–3. **Highest-risk phase; verification gates everything after it.**
- Blast-radius audit first: grep both adapters + tests for `.pk`, `pk=`, `pk__in`,
  `refresh_from_db`, `in_bulk`, FK traversals of `QueuedJob`. Known hit: `save_meta`
  (`models.py:823`) rewritten to `filter(id=…, created_at=…)`.
- Composite PK `("created_at", "id")`; `JobRegistry.job` and `Worker.current_job`
  FKs demoted to indexed `BigIntegerField` (orphaning on partition drop is the
  accepted, documented trade-off).
- Migration `0029_partition_queued_job.py` exactly as revised in PLAN.md Step 8:
  idempotent guarded rename, `LIKE … INCLUDING DEFAULTS INCLUDING STORAGE` (NOT
  IDENTITY), `ADD GENERATED BY DEFAULT AS IDENTITY` + `setval`, historical
  partitions created BEFORE the bulk copy, DEFAULT partition, the Phase-1 index
  definition verbatim, stop-the-world window documented, legacy table dropped in
  a separate later migration so rollback is a rename.
- **Success criteria:** migration round-trip (legacy → partitioned → rollback)
  passes on a generated production-sized snapshot (≥1M rows); zero rows in DEFAULT
  after migration; identity continues from max(id)+1; re-running the migration
  after an injected mid-migration failure completes cleanly; the `.pk` audit has
  zero unaddressed hits.

### Phase 5 — Backend wiring + claim-path pruning (PLAN.md Steps 9–10) — requirements R3, R6
Depends on: Phase 4.
- `_partitioned_pg()` helper; `cleanup_jobs` routes to `reclaim_drained_partitions`;
  `vacuum_database` skips the partitioned jobs table. Mirror in `async_backend.py`.
- Add `created_at` to every id-only write path — the complete 11-item checklist in
  PLAN.md Step 10 (db_compat.py:100,139; models.py:623,656,707,823; backend.py:294,
  632,637-642,772,792,898). Verify no `.only()`/`values()` trims `created_at`.
- **Success criteria:** EXPLAIN on each checklist write path shows single-partition
  pruning; full claim → run → complete → reclaim lifecycle test passes on a
  partitioned table; SQLite divergence matrix green.

### Phase 6 — FastAPI parity (PLAN.md Step 11) — requirements R1–R6 for the SQLAlchemy backend
Depends on: Phase 5.
- Mirror Phases 4–5 in `fastapi_sqlery/`: `database.py` emits partitioned DDL +
  partial index for new installs; `config.py` gains the partition keys; backend +
  async backend route cleanup and prune writes.
- **Success criteria:** same lifecycle test as Phase 5 passes against the FastAPI
  backend; fresh install via `database.py` creates a partitioned table by default.

### Phase 7 — LISTEN/NOTIFY (PLAN.md Step 12) — optional, no requirement; latency improvement
Depends on: Phase 6. Skippable without affecting milestone "done".
- Opt-in `SQLERY_PG_NOTIFY`; `pg_notify` after enqueue; worker LISTEN loop with
  poll fallback. Reference: `clients/python/pgwq/worker.py` and the `pg_notify`
  line in `sql/pgwq.sql`.
- **Success criteria:** with flag on, dispatch latency < 100 ms in test; with flag
  off (default), behavior is byte-identical to before.

> PLAN.md Step 13 (tests/rollback/metrics) is NOT a phase — its items are already
> distributed into the success criteria above and must be implemented inside each phase.

## Locked decisions (do not re-ask; do not re-litigate)

1. Partition by RANGE on `created_at`, daily intervals. Defaults:
   `SQLERY_PARTITION_INTERVAL="1 day"`, `SQLERY_PARTITION_RETENTION="30 days"`,
   `SQLERY_PARTITION_PREMAKE=7`, `PARTITION_MAINTENANCE_INTERVAL_MINUTES=5`,
   staging threshold = 1 day, `SQLERY_PG_NOTIFY=False`, `SQLERY_PARTITION_ARCHIVE_HOOK=None`.
2. Hand-rolled partition maintenance, NOT pg_partman (library can't demand an
   extension; partman lacks the invariant-checked drop). Decision is recorded in PLAN.md.
3. Step 8 is a stop-the-world migration with a maintenance window. No online
   dual-write cutover. Escape hatch for huge tables: run SQL manually + `migrate --fake`.
4. FK referential integrity from `JobRegistry`/`Worker` to jobs is dropped
   (plain `BigIntegerField`); orphans on partition drop are accepted and documented.
5. Failed-job history older than retention is destroyed on partition drop unless
   the operator configures the archive hook. Default is destroy; document loudly.
6. SQLite keeps the (batched) DELETE path forever; no partitioning emulation.
7. Status literal is `'queued'` (verified at models.py:351); claim ordering is
   `-priority, created_at` (verified at backend.py:870-874). Index trailing column
   is `created_at`, identical in Phase 1 and Phase 4 DDL.
8. New PG installs partition by default; existing installs partition on migrating.
   No feature flag for partitioning itself — only LISTEN/NOTIFY is flagged.
9. Concurrency control is `pg_try_advisory_lock` per maintenance function; a
   daemon that loses the lock skips the tick silently.
10. Phase ordering is fixed as above; Phases 1 and 7 are the only ones that may
    float (1 first always; 7 may be deferred or dropped).

## Conventions for execution (from repo owner's global standards)

- Python 3.13+ syntax, modern built-in type annotations, uv for env/deps.
- Commits: conventional format `(type): description`, single line, < 50 chars,
  never mention Claude/AI.
- When changing existing lines: comment out the wrong line, add the corrected
  line beneath — never delete/replace lines outright.
- Track any regression discovered during the milestone in `REGRESSIONS.md`.
- Pure functions preferred; cyclomatic complexity ≤ 10; tests describe behavior.

## Verification anchors (use in every phase's verify step)

- Test matrix: drop-skips-live-partition; far-future pin; claim-after-partition;
  migration round-trip on snapshot; DEFAULT-partition alert; SQLite × PG matrix
  over every backend method.
- Metrics to exist by Phase 5: partition count, DEFAULT-partition row count,
  oldest undrained partition age, staging-table depth, maintenance-tick duration.
- The 11-item write-path checklist (PLAN.md Step 10) is a literal acceptance
  checklist for Phase 5 — every item gets an EXPLAIN-verified pruning test.
