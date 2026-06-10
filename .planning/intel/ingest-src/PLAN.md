# sqlery production-grade plan

Derived from the PgQue analysis (`sqlery-vs-pgque.md`) and discovery session.
All findings are source-verified against the current `sqlery-public` repo.
Ship steps in order — each is independently releasable.

---

## What this fixes

The two highest-risk slow killers:

1. **xmin pinning death spiral** — an OLAP query or idle-in-transaction session
   pins the global xmin; VACUUM can't reclaim UPDATE/DELETE churn; bloat compounds
   indefinitely (the PlanetScale scenario, P4 in the analysis).
2. **Full index bloat** — the hot claim index contains every job in every status,
   degrading scan performance over months as finished rows pile up.

**Core fix:** daily time-range partitions, dropped wholesale when drained, replacing
unbounded DELETE cleanup. Bloat becomes bounded by `throughput × retention` regardless
of uptime. xmin pinning can only affect partitions inside the retention window — which
then get dropped.

**Architecture:** partition logic lives in `core/`, Django and FastAPI adapters
consume it. New PG installs partition by default. SQLite uses the existing DELETE path
unchanged.

**Defaults:** `SQLERY_PARTITION_INTERVAL = "1 day"`, `SQLERY_PARTITION_RETENTION = "30 days"`,
`SQLERY_PARTITION_PREMAKE = 7`.

---

## Step 1 — Partial index (1–2 hrs, zero risk)

**File:** `src/sqlery/django_sqlery/models.py:592`

Replace the full composite index with a partial one. The hot claim path scans only
pending rows regardless of how many finished rows are in the table.

```python
# models.py — Meta.indexes
# models.Index(fields=["queue_name", "status", "-priority", "created_at"]),  # bloats with every finished row
# models.Index(
#     fields=["queue_name", "-priority", "scheduled_at"],  # WRONG: claim query orders by created_at (backend.py:870-874), and Step 8 DDL uses created_at
#     name="sqlery_job_pending_idx",
#     condition=Q(status="queued"),
# )
models.Index(
    fields=["queue_name", "-priority", "created_at"],  # matches order_by("-priority", "created_at") and Step 8's DDL verbatim
    name="sqlery_job_pending_idx",
    condition=Q(status="queued"),
)
```

<!-- New migration: `0028_partial_pending_index.py` (standard Django migration, no special flags). -->
<!-- WRONG: plain AddIndex takes a write-blocking lock for the whole build on the hottest table. -->
New migration: `0028_partial_pending_index.py` with `atomic = False`, using
`django.contrib.postgres.operations.AddIndexConcurrently` for the new index and
`RemoveIndexConcurrently` for the old one. The index definition must be byte-identical
to Step 8's DDL so the partitioned table carries it forward without a name collision.

---

## Step 2 — Batched DELETE cleanup (half-day)

**Files:** `src/sqlery/django_sqlery/backend.py:455`, `src/sqlery/fastapi_sqlery/backend.py:674`

Replace the unbounded `.delete()` call with a keyset-batched loop in both backends.
Prevents large dead-tuple bursts and long locks under backlog.

```python
# backend.py — cleanup_jobs
# query.delete()  # old: unbounded DELETE, big dead-tuple burst under backlog
# BATCH = 500
# while True:
#     ids = list(query.values_list("id", flat=True)[:BATCH])  # WRONG: no ORDER BY (nondeterministic LIMIT), no throttle, races the claim path
#     if not ids:
#         break
#     self.QueuedJob.objects.filter(id__in=ids).delete()  # WRONG: no status re-check — can delete a job claimed between SELECT and DELETE
BATCH = 500
while True:
    ids = list(query.order_by("id").values_list("id", flat=True)[:BATCH])
    if not ids:
        break
    # re-apply the finished-status predicate so a row claimed mid-loop is never deleted
    self.QueuedJob.objects.filter(id__in=ids, status__in=FINISHED_STATUSES).delete()
    time.sleep(0.1)  # let autovacuum keep pace instead of one sustained dead-tuple firehose
```

This is the **permanent SQLite / non-partitioned-PG path**, not a stopgap: once the
partition path (Steps 3–4) lands, partitioned-PG installs bypass it entirely, but it
remains the fallback for everything else. Applies to both backends (the FastAPI
`backend.py:674` unbounded DELETE gets the same treatment).

---

## Step 3 — `core/partitioning.py` (new file)

Framework-agnostic, takes a raw DB cursor. No Django or SQLAlchemy imports.

```
ensure_future_partitions(cur, table, interval, premake)
    CREATE TABLE IF NOT EXISTS for the next N time intervals.
    Guarded by pg_try_advisory_lock (skip tick if not acquired) — multiple daemons
    must not race partition DDL.
    Must catch the attach-conflict error (rows sitting in DEFAULT overlapping the
    new range make CREATE ... PARTITION OF fail) and surface an alert instead of
    wedging the maintenance loop.

reclaim_drained_partitions(cur, table, retention)
    Invariant-checked reclaim, guarded by pg_try_advisory_lock:
    - skip DEFAULT partition
    - skip partitions inside retention window
    - skip partitions with any queued/running rows  ← back-pressure invariant
    - DETACH PARTITION first (shrinks the lock window), then run the optional
      archive hook (copy/dump of failed-job rows), then DROP
    - document loudly: failed-job history older than retention is destroyed
      unless the archive hook is configured

check_default_partition(cur, table) → int
    Row count in the DEFAULT partition. Anything > 0 is a standing alert: those
    rows are never reclaimed and will block future partition creation.

_list_partitions(cur, table) → list[tuple[str, datetime]]
    Read partition names + upper bounds from pg_inherits + pg_get_expr.
```

---

## Step 4 — `core/cleanup.py` (edit)

When the backend signals `partitioned_pg=True`, route cleanup to partition
maintenance instead of the DELETE path. Backend exposes a `_partitioned_pg()`
helper; `core/cleanup.py` calls it before deciding which path to take.

---

## Step 5 — `core/daemon.py` (edit)

Add `PARTITION_MAINTENANCE_INTERVAL_MINUTES` config key (default `5`). Alongside
the existing `AUTO_CLEANUP_JOBS` gate, run `ensure_future_partitions` +
`reclaim_drained_partitions` on this cadence. Must be ≤ partition interval so
partitions are always provisioned before rows need them.

Every maintenance function (including `promote_due_scheduled_jobs`, Step 6) is
wrapped in `pg_try_advisory_lock` and skips the tick if the lock isn't acquired —
N daemons must not concurrently run partition DDL or promotion.

---

## Step 6 — Far-future `scheduled_at` jobs: staging table

A job created today but `scheduled_at = T+60d` keeps its old partition alive
indefinitely — it's a `queued` row pinning an otherwise-drained partition.

**Fix (as recommended):** a separate lightweight `sqlery_scheduled_job` staging
table for jobs with `scheduled_at > now() + threshold` (default: 1 day).
The scheduler (daemon loop) promotes rows from the staging table into the hot
partitioned table when `scheduled_at <= now() + lookahead`. The hot partitioned
table never holds far-future rows, so the partition invariant holds cleanly.

New files / edits:
- `core/scheduler.py` — `promote_due_scheduled_jobs(cur)`, runs every minute in
  the daemon loop.
- `django_sqlery/models.py` — new `ScheduledJob` model (slim: queue, payload,
  scheduled_at, priority, max_retries). Plain table, no partitioning.
- `django_sqlery/migrations/0030_scheduled_job_staging.py` — create the staging table.
- `queue.py` / enqueue path — when `scheduled_at > now() + threshold`, INSERT into
  `sqlery_scheduled_job` instead of `sqlery_queued_job`.

**Promotion semantics (exactly-once):** one transaction —
`DELETE FROM sqlery_scheduled_job WHERE scheduled_at <= now() + lookahead
FOR UPDATE SKIP LOCKED RETURNING *` → `INSERT INTO sqlery_queued_job (...)`.
`SKIP LOCKED` makes concurrent daemons safe; the advisory lock (Step 5) makes
double-running cheap anyway. Job ids must come from one shared sequence so an
id never exists in both tables.

**Dual-table API surface:** every consumer-facing read/write must span both
tables — status lookup, fetch-by-id, cancel/delete, and list endpoints in both
adapters. Enumerate and patch each one as part of this step; a job "disappearing"
while staged is a bug.

**Invariant (state it, don't imply it):** `threshold ≪ retention`. With a 1-day
threshold and 30-day retention, an in-table job can pin its partition at most
~1 day past drain — comfortably inside retention. Setting retention near or
below the threshold breaks partition reclaim; validate this at config load.

---

## Step 7 — Django model: composite PK + FK fix

**File:** `src/sqlery/django_sqlery/models.py:344`

The partition key (`created_at`) must be in the PK. Django 5.2 (already required)
supports composite PKs:

```python
pk = models.CompositePrimaryKey("created_at", "id")
id = models.BigAutoField(primary_key=False)
```

`JobRegistry.job` and `Worker.current_job` are FK references to `QueuedJob`. A
composite PK breaks single-column FKs — convert both to plain indexed
`BigIntegerField`. (`parent_job_id` is already a plain int, no change needed.)

**Precondition — blast-radius audit:** before writing this step's code, grep both
adapters and tests for `.pk`, `pk=`, `pk__in`, `refresh_from_db`, `in_bulk`, and
FK traversals of `QueuedJob`. Known hit: `save_meta` filters on `filter(pk=self.pk)`
(models.py:823) — `.pk` becomes a tuple under a composite PK; rewrite as
`filter(id=self.id, created_at=self.created_at)`.

**Accepted trade-off (record it):** demoting the FKs to `BigIntegerField` drops
referential integrity and `CASCADE`/`SET_NULL` — `JobRegistry` and `Worker`
rows referencing jobs in a dropped partition become orphans. Either accept and
document this, or add a registry-cleanup pass that runs after each partition drop.
Version-based optimistic locking is unaffected (it filters on `id` + `version`,
never `pk`), but verify the SQLite path in tests.

---

## Step 8 — Migration `0029_partition_queued_job.py` (`atomic=False`, PG-only)

Guard on `connection.vendor == "postgresql"`; no-op on SQLite.

**This is a stop-the-world migration.** Stop all workers/daemons before running it
and restart them after — between the rename and the end of the bulk copy, live
writes would either error or land in the new empty table while the copy races them.
Document expected duration as a function of table size (the `INSERT ... SELECT`
copies every row). Every statement is idempotent (`IF NOT EXISTS`, guarded rename)
so a partial failure under `atomic=False` is recoverable by re-running.

Skeleton:

```sql
-- ALTER TABLE sqlery_queued_job RENAME TO sqlery_queued_job_legacy;  -- WRONG: not idempotent; fails on re-run after partial failure
DO $$ BEGIN
    IF to_regclass('sqlery_queued_job_legacy') IS NULL THEN
        ALTER TABLE sqlery_queued_job RENAME TO sqlery_queued_job_legacy;
    END IF;
END $$;

-- CREATE TABLE sqlery_queued_job (
--     LIKE sqlery_queued_job_legacy
--     INCLUDING DEFAULTS INCLUDING IDENTITY INCLUDING STORAGE  -- WRONG: INCLUDING IDENTITY creates a NEW identity sequence, then SET DEFAULT nextval fails on an identity column
-- ) PARTITION BY RANGE (created_at);
CREATE TABLE IF NOT EXISTS sqlery_queued_job (
    LIKE sqlery_queued_job_legacy
    INCLUDING DEFAULTS INCLUDING STORAGE
) PARTITION BY RANGE (created_at);

ALTER TABLE sqlery_queued_job ADD PRIMARY KEY (created_at, id);
-- ALTER TABLE sqlery_queued_job ALTER COLUMN id
--     SET DEFAULT nextval('sqlery_queued_job_id_seq');  -- WRONG: id is GENERATED BY DEFAULT AS IDENTITY (Django 5.2 BigAutoField) — this sequence does not exist
ALTER TABLE sqlery_queued_job ALTER COLUMN id
    ADD GENERATED BY DEFAULT AS IDENTITY;

-- default partition catches anything outside provisioned ranges
CREATE TABLE IF NOT EXISTS sqlery_queued_job_default
    PARTITION OF sqlery_queued_job DEFAULT;

-- partial hot-path index, inherited by all partitions (identical to Step 1's definition)
CREATE INDEX IF NOT EXISTS sqlery_job_pending_idx ON sqlery_queued_job
    (queue_name, priority DESC, created_at) WHERE status = 'queued';

-- HISTORICAL PARTITIONS BEFORE THE COPY — otherwise every existing row lands in
-- DEFAULT, is never reclaimed, and blocks future CREATE ... PARTITION OF with an
-- overlap error. Generated by the migration: one partition per interval from
-- date_trunc('day', min(created_at)) on the legacy table through now() + premake.
-- (Loop emitted in plpgsql or from Python over the legacy table's date range.)

-- INSERT INTO sqlery_queued_job SELECT * FROM sqlery_queued_job_legacy;  -- WRONG only in ordering: must run AFTER historical partitions exist
INSERT INTO sqlery_queued_job SELECT * FROM sqlery_queued_job_legacy
    ON CONFLICT DO NOTHING;  -- idempotent re-run

-- seed the new identity past the copied rows
SELECT setval(pg_get_serial_sequence('sqlery_queued_job', 'id'),
              (SELECT COALESCE(MAX(id), 1) FROM sqlery_queued_job));

DROP TABLE IF EXISTS sqlery_queued_job_legacy;
```

`state_operations` must mirror the model changes in Step 7.
<!-- For large existing tables, document expected duration and provide a
`--fake-initial` escape hatch. -->
<!-- WRONG: --fake-initial only applies to initial migrations; it does nothing here. -->
For large existing tables, the escape hatch is: run the SQL manually during a
maintenance window, then `migrate --fake` this specific migration.

**Rollback (write it down before shipping):** the reverse is the same swap in the
other direction — create an unpartitioned table `LIKE` the partitioned one, copy
rows back, rename. Keep the legacy table until the new path has soaked (i.e. make
`DROP TABLE ... _legacy` a separate, later migration) so rollback is a rename, not
a copy.

---

## Step 9 — Django backend + async backend

**Files:** `src/sqlery/django_sqlery/backend.py`, `async_backend.py`

- `_partitioned_pg()` helper: `connection.vendor == "postgresql"` (default true for PG).
- `cleanup_jobs` (`:455`): when `_partitioned_pg()`, call
  `reclaim_drained_partitions` instead of DELETE.
- `vacuum_database` (`:529`): skip VACUUM on `sqlery_queued_job` when partitioned
  (DROP leaves nothing to vacuum; keep VACUUM for other tables).
- Mirror all changes in `async_backend.py`.

---

## Step 10 — Claim path tweak

**File:** `src/sqlery/django_sqlery/db_compat.py`

The status-flip UPDATE filters only on `id`. Under partitioning PG scans all
partitions. Add `created_at` to prune to one partition:

```python
# db_compat.py — atomic_claim_job
# QueuedJob.objects.filter(id=job.id, version=job.version).update(...)  # full partition scan
QueuedJob.objects.filter(id=job.id, created_at=job.created_at, version=job.version).update(...)
```

This is not one line — it's every id-only write path. Full checklist (each adds
`created_at` to the filter; `get_claimable_jobs` fetches full model rows at
backend.py:846-877, so `job.created_at` is already in hand — no extra query):

- `db_compat.py:100` `atomic_claim_job_sqlite` — `filter(id=..., version=...)` CAS
- `db_compat.py:139` `atomic_claim_job_postgres` — `filter(id=..., version=...)` CAS
- `models.py:623` `mark_running`, `models.py:656` `mark_success`,
  `models.py:707` `mark_failed` — version-CAS updates by `id`
- `models.py:823` `save_meta` — `filter(pk=self.pk)` (also rewritten in Step 7)
- `backend.py:294` cancel queued job; `backend.py:632` archive failed;
  `backend.py:637-642` retry-chain status walk; `backend.py:772` fetch by id;
  `backend.py:792` `child_pid` update; `backend.py:898` job field update

Ensure no `.only()`/`values()` call on the claim path trims `created_at` out.

---

## Step 11 — FastAPI adapter

**Files:** `src/sqlery/fastapi_sqlery/backend.py:674`, `async_backend.py`,
`database.py`, `config.py`

Mirror Steps 9–10 for the SQLAlchemy backend. `database.py` emits partitioned
DDL + partial index instead of plain `create_all` for the jobs table.
`config.py` adds the new partition config keys.

---

## Step 12 — LISTEN/NOTIFY (optional, PG-only)

Cut worker dispatch latency from up to 5 s (poll interval) to sub-100 ms.
Gate behind `SQLERY_PG_NOTIFY = False` (opt-in).

When enabled:
- After `pgwq.enqueue`, call `pg_notify('sqlery_job_<queue>', '')`.
- Worker opens a LISTEN connection and wakes on NOTIFY, falling back to polling
  on timeout.

See `sqlery-pgque/sql/pgwq.sql` (the `pg_notify` line in `enqueue`) and
`clients/python/pgwq/worker.py` (the LISTEN/NOTIFY loop) as the reference
implementation.

---

## Step 13 — Testing, rollback, observability (ships with Steps 3–7, not after)

**Test matrix** (from the analysis doc, plus divergence coverage):
- reclaim skips a partition holding queued/running rows (back-pressure invariant)
- far-future `scheduled_at` job never pins a hot partition (staging path)
- claim → complete round-trip lands on a partitioned table (CAS with `created_at`)
- migration round-trip on a production-sized snapshot: legacy → partitioned → rollback
- DEFAULT-partition alert fires when a row lands there
- SQLite × PG divergence matrix: every backend method runs under both vendors

**Rollback per step:** Steps 1–2 are plain reverts. Step 8's rollback is the reverse
swap documented in Step 8 (legacy table retained until soak completes).

**Metrics** (exposed via the existing daemon stats path):
- partition count
- DEFAULT-partition row count (alert > 0 — see finding #2)
- oldest undrained partition age
- staging-table depth
- maintenance-tick duration

---

## Why hand-rolled instead of pg_partman

Recorded decision, not an oversight: sqlery is a library and cannot demand a PG
extension be installed on user databases; and `pg_partman` drops partitions purely
by age — it has no equivalent of the invariant-checked drop (skip partitions with
queued/running rows), which is the load-bearing safety property here. Hand-rolling
~100 lines in `core/partitioning.py` is the cheaper trade.

---

## Config keys added

| Key | Default | Meaning |
|-----|---------|---------|
| `SQLERY_PARTITION_INTERVAL` | `"1 day"` | Partition width |
| `SQLERY_PARTITION_PREMAKE` | `7` | Future partitions to keep ready |
| `SQLERY_PARTITION_RETENTION` | `"30 days"` | Min age before a drained partition may drop |
| `SQLERY_PG_NOTIFY` | `False` | Opt-in LISTEN/NOTIFY wake-up (Step 12) |
| `SQLERY_PARTITION_ARCHIVE_HOOK` | `None` | Optional callable run on each detached partition before DROP (Step 3) |

Config validation at load: `SQLERY_PARTITION_RETENTION` must be ≫ the staging
threshold (Step 6 invariant) and `PARTITION_MAINTENANCE_INTERVAL_MINUTES` must be
≤ `SQLERY_PARTITION_INTERVAL` (Step 5 invariant).

To flip partition interval to hourly: change default `"1 day"` → `"1 hour"` in
one place (settings defaults).

---

## Ship order

| # | What | Risk | Effort |
|---|------|------|--------|
| 1 | Partial index | Zero | 1–2 hr |
| 2 | Batched DELETE cleanup | Low | Half-day |
| 3 | `core/partitioning.py` + daemon tick | Low | 1 day |
| 4 | Far-future staging table (Step 6) | Medium | 1–2 days |
| 5 | Django model + migration (Steps 7–8) | Medium | 1 day |
| 6 | Django backend + claim tweak (Steps 9–10) | Low | Half-day |
| 7 | FastAPI adapter (Step 11) | Low | Half-day |
| 8 | LISTEN/NOTIFY opt-in (Step 12) | Low | 1 day |
| — | Tests/rollback/metrics (Step 13) | — | Woven into rows 3–6, not a trailing row |

Steps 1–2 can ship to `sqlery-public` immediately, independent of the rest.
Steps 3–7 are one logical feature; ship behind a single PR or a short stack.

**Dependency note:** Step 1's index is dropped and recreated by Step 8's DDL — this
is intentional churn, and safe only because the two definitions are now byte-identical
(`queue_name, priority DESC, created_at WHERE status = 'queued'`). If either
definition changes, change both.

---

## Reference artifacts (this directory)

| File | What it is |
|------|-----------|
| `sqlery-vs-pgque.md` | Full analysis: PgQue mechanics, sqlery exposure scorecard, Appendix A/B (partition design), Appendix C/D (pgwq SQL + Python client) |
| `sql/pgwq.sql` | Standalone pure-SQL reference: partitioned work queue with invariant-checked rotation, verified on PG 16 |
| `clients/python/pgwq/` | Python client for pgwq — reference for LISTEN/NOTIFY worker loop and enqueue/claim/complete/fail API |

---

## Senior review — feedback and required revisions

> **Status: ADDRESSED.** All 14 findings below have been folded into the plan body
> above (rev of 2026-06-10): Steps 1, 2, 3, 5, 6, 7, 8, 10 revised in place
> (wrong lines commented out, corrected versions added), Step 13 + the pg_partman
> rationale + config validation added. Kept for the record.

Verified against the actual codebase at `sqlery-public` (Django >= 5.2 per `pyproject.toml`; `default_auto_field = BigAutoField` in `src/sqlery/django_sqlery/apps.py:34`). Findings ordered by severity.

### 1. BLOCKER — Step 8 migration SQL is broken as written

- **Identity, not serial.** `QueuedJob.id` is an implicit `BigAutoField` (apps.py:34) and Django >= 4.1 emits `GENERATED BY DEFAULT AS IDENTITY`, not `bigserial`. There is no `sqlery_queued_job_id_seq` to point a default at; the plan's `SET DEFAULT nextval(...)` will fail (and `INCLUDING IDENTITY` interacts badly with it anyway). The new partitioned table must recreate the identity (or attach a fresh sequence) explicitly and `setval` it past `max(id)`.
- **Rename-then-copy loses writes.** Between `ALTER TABLE ... RENAME` and the bulk `INSERT ... SELECT`, live daemons (`get_claimable_jobs`, `mark_running`, etc.) are still writing. Either take an exclusive lock for the whole swap or stop workers first — the plan must say which.
- **`atomic = False` with multi-statement `RunSQL` has no recovery story.** If statement 3 of 6 fails, the schema is half-renamed. Each step needs an idempotent guard or a documented manual-recovery runbook.
- **`--fake-initial` is wrong here** — this is not an initial migration; the correct escape hatch is "run the SQL manually, then `migrate --fake` the specific migration", or `SeparateDatabaseAndState`.
- Migration numbering is fine: latest is `0027_rename_sqlery_daem_...`, so 0028/0029/0030 are correct.

### 2. BLOCKER — DEFAULT partition trap

The bulk `INSERT ... SELECT` runs before any historical partitions exist, so all existing rows land in the DEFAULT partition — where `reclaim_drained_partitions` (which skips DEFAULT) never reclaims them, reintroducing the exact bloat this plan exists to kill. Worse: a later `CREATE TABLE ... PARTITION OF ... FOR VALUES` whose range overlaps rows sitting in DEFAULT **errors out** — a single stray row can wedge `ensure_future_partitions` permanently. Fix: (a) create all historical partitions before the copy; (b) maintenance must detect rows in DEFAULT (alert > 0, optionally re-insert them into proper partitions); (c) `ensure_future_partitions` must handle the attach-conflict error path.

### 3. MAJOR — Step 1 index does not match Step 8 or the live query

Verified: the existing index is `models.Index(fields=["queue_name", "status", "-priority", "created_at"])` (models.py:592), and the actual claim ordering is `order_by("-priority", "created_at")` (optionally prefixed by `-queue_priority`) in `get_claimable_jobs`, `src/sqlery/django_sqlery/backend.py:870-874` — **not** `scheduled_at`. The plan's proposed Step 1 index trailing column (`scheduled_at`) matches neither the query nor Step 8's DDL (`created_at`). Pick `created_at` consistently in both steps. The status literal is confirmed as `'queued'` (models.py:351, default at 385-386), so that part is correct — but the two index definitions sharing one name with different columns will collide in Step 8.

### 4. MAJOR — index creation must be concurrent

On a large hot table, plain `AddIndex` takes a write-blocking lock for the whole build. Use `django.contrib.postgres.operations.AddIndexConcurrently` (and `RemoveIndexConcurrently`) in a migration with `atomic = False`. The plan's "standard Django migration, no special flags" claim is wrong.

### 5. MAJOR — batched delete is half-designed

The batched `DELETE ... WHERE id IN (SELECT ... LIMIT n)` has no `ORDER BY` in the subselect, no inter-batch sleep/throttle (turning one burst into a sustained dead-tuple firehose that outruns autovacuum anyway), and can race the claim path: `get_claimable_jobs` uses `FOR UPDATE SKIP LOCKED` (db_compat.py:58) but the delete subselect doesn't — re-apply the status predicate inside the batch DELETE. Also note this code becomes dead weight once partition-drop retention (Steps 4/9) lands; keep it only as the SQLite/non-partitioned fallback and say so.

### 6. MAJOR — staging-table promotion semantics unspecified

The plan doesn't define exactly-once promotion from staging to the main table (it must be `DELETE ... RETURNING` + `INSERT` in one transaction with `FOR UPDATE SKIP LOCKED`), what API consumers see for a job still in staging (status reads, cancel, list, fetch-by-id must span both tables), or how ids stay unique across two tables. State the threshold/retention invariant explicitly (1-day threshold ≪ 30-day retention is what makes the partition invariant hold) so someone doesn't later set retention to 2 days and break it.

### 7. MAJOR — CompositePrimaryKey blast radius is understated

`JobRegistry.job` (models.py:976) and `Worker.current_job` (models.py:1047) are real FKs to `QueuedJob`; only `parent_job_id` is a plain `IntegerField` (models.py:394). Switching the PK to `(created_at, id)` breaks both FKs, makes `.pk` a tuple (affecting `save_meta`'s `filter(pk=self.pk)` at models.py:823, `refresh_from_db`, admin, and third-party code). Demoting the FKs to `BigIntegerField` drops referential integrity and `CASCADE`/`SET_NULL` behavior — `JobRegistry` rows would orphan on partition drop. Add a precondition audit task (grep for `.pk`, `pk=`, and FK usages of `QueuedJob` across both adapters and tests) and state the orphaning trade-off explicitly.

### 8. MAJOR — multi-process coordination missing

Partition maintenance (create-ahead, drop-expired) and scheduled-job promotion will run from every daemon process unless guarded. Concurrent `DROP TABLE` of the same partition errors; concurrent promotion double-runs. Wrap each maintenance function in `pg_try_advisory_lock` (skip if not acquired). One line, mandatory.

### 9. MAJOR — retention drop destroys failed-job history

`DROP TABLE partition_x` deletes failed jobs alongside succeeded ones; today `cleanup_jobs` can filter by status. Add a decision point: document loudly that failed jobs older than retention are gone, or offer archive-before-drop (`DETACH PARTITION` + copy/dump, which also shrinks the lock window).

### 10. MAJOR — no testing / rollback / observability section

The analysis doc already lists the right tests (drop-skips-live-partition, far-future pin, claim-after-partition, migration round-trip) but the plan dropped them. Add a step covering: (a) that test matrix plus an SQLite-vs-PG divergence matrix (every backend method × both vendors); (b) rollback per step — especially Step 8: how to get back to the unpartitioned table; (c) metrics: partition count, DEFAULT-partition row count (alert > 0), oldest undrained partition age, staging-table depth, maintenance-tick duration.

### 11. MINOR — pg_partman vs. hand-rolled not justified

The plan hand-rolls partition maintenance without recording why `pg_partman` was rejected. The no-extension-dependency constraint and the invariant-checked drop (which partman doesn't do) are good reasons — write them down so it's a decision, not an oversight.

### 12. MINOR — ship-order index churn

Step 1's index is dropped/recreated by Step 8's DDL. Acceptable as a standalone quick win, but only once finding #3 is fixed so both definitions are identical — make the dependency explicit in the ship-order table so Step 8 doesn't fail on a duplicate index name.

### 13. CONFIRMED — Step 10 (composite-key write paths) is under-specified

Every hot write path filters by `id` alone and would scan all partitions. The complete inventory (each must add `created_at` to the filter):

- `db_compat.py:100` (`atomic_claim_job_sqlite`) and `db_compat.py:139` (`atomic_claim_job_postgres`) — `filter(id=..., version=...)` CAS updates.
- `models.py:623` (`mark_running`), `models.py:656` (`mark_success`), `models.py:707` (`mark_failed`) — version-CAS updates by `id`.
- `models.py:823` (`save_meta`) — `filter(pk=self.pk)`.
- `backend.py:294` (cancel queued job), `backend.py:632` (archive failed), `backend.py:637-642` (retry-chain status walk via `parent_job_id`), `backend.py:772` (fetch by id), `backend.py:792` (`child_pid` update), `backend.py:898` (job field update).

Good news: `get_claimable_jobs` (backend.py:846-877) fetches full model rows, so `created_at` is already in hand — no extra query needed, but Step 10 must say "thread `job.created_at` into every subsequent UPDATE" with this inventory as the checklist.

### 14. CONFIRMED — unbounded delete exists where the plan says

`cleanup_jobs` at `django_sqlery/backend.py:455` runs an unbounded `query.delete()`, and `fastapi_sqlery/backend.py:674` builds a single unbounded `DELETE`. Finding #5's batching requirements apply to both backends, not just Django.
