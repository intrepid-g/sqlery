# sqlery vs PgQue — Postgres-as-a-Queue analysis & production-grade plan

> Purpose: understand **PgQue** and the problems it identifies with using Postgres
> as a queue, then assess **sqlery** (latest, v0.22.4) against each — *does sqlery
> inevitably degrade? what mitigations exist?* — and outline a path to make sqlery
> production-grade, including a possible **Postgres-only** variant.
>
> Status: source-verified where marked ✅ (read on 2026-06-03). Unconfirmed items
> are flagged ❓ — these are the things to check in the repo next.
>
> sqlery architecture (corrected): **`core/` (framework-agnostic)** + adapters
> **`django_sqlery/`** and **`fastapi_sqlery/`**. The interesting production
> behavior lives in `core/` + the concrete `DatabaseBackend` implementations.

---

## Part 1 — Understanding PgQue

### 1.1 What it is
A **zero-bloat Postgres queue / event-streaming engine**, pure PL/pgSQL, derived
from Skype's PgQ. Installed as one SQL file (`sql/pgque.sql`), no daemon, no C
extension. Postgres 14+. Apache-2.0. v0.2.0.

### 1.2 The core idea (how it works) ✅
Verified from `docs/concepts.md`, `docs/latency-and-tuning.md`, `sql/pgque.sql`:

- **Event** = a row appended to a queue: `ev_id, ev_time, ev_txid (xid8),
  ev_retry, ev_type, ev_data, ev_extra1..4`. At-least-once. Append-only.
- **Tick** = a periodic marker storing a Postgres **snapshot**
  (`tick_snapshot pg_snapshot`) of which txids were visible at that instant.
  Default 10 ticks/sec (every 100 ms).
- **Batch** = the events between two consecutive ticks, computed as a **diff of
  two snapshots** — "events visible in the current tick's snapshot but not in the
  previous tick's." *No row locks, no per-row deletes anywhere in the delivery path.*
- **Consumer cursor** = each consumer keeps its own `sub_last_tick`; everyone
  reads the same single copy of each event → **native fan-out** (Kafka-like).
- **Rotation** = three inheritance child tables `event_<q>_0..2`. The active
  child fills; old ones are reclaimed by **`TRUNCATE`** once *every* consumer has
  read past them. `TRUNCATE` drops storage at once → **no dead tuples, no VACUUM**.
- **Ticker rule** (non-negotiable): keep the ticker running (cron →
  `pgque.ticker_loop()`). No ticks = no batches = no delivery; long pauses create
  one huge undigestible batch. A *slow consumer blocks rotation* (back-pressure).

### 1.3 Latency model ✅
Three latencies: producer `send()` ~sub-ms, subscriber `receive()` ~sub-ms,
**end-to-end ≈ tick period** (dominated by tick cadence, NOT by load).

| Tick period | p50 e2e | p95 | max |
|-------------|---------|-----|-----|
| 1000 ms     | ~503 ms | —   | —   |
| **100 ms** (default) | **~52 ms** | ~99 ms | ~105–145 ms |
| 10 ms       | ~8 ms   | —   | —   |
| 1 ms        | ~3 ms   | —   | —   |

Knobs: `tick_period_ms`, `ticker_max_count` (batch size cap, default 500),
`ticker_max_lag` (3 s), `ticker_idle_period` (1 min idle backoff). Higher tick
rates cost WAL/NOTIFY/cron churn. **"If your top priority is single-digit-ms
dispatch, PgQue is the wrong tool."**

---

## Part 2 — The problems PgQue identifies with "Postgres as a queue"

This is the heart of what you asked. PgQue's thesis (README, verified) is that the
**standard `SKIP LOCKED` + `UPDATE`/`DELETE` queue pattern** — which sqlery uses —
"holds up in toy examples and then turns into dead tuples, VACUUM pressure, index
bloat, and performance drift under sustained load."

| # | Problem | Mechanism | PgQue's evidence | PgQue's fix |
|---|---------|-----------|------------------|-------------|
| P1 | **Dead-tuple bloat** | Every claim is an `UPDATE` (status→running) and every finished job is `DELETE`d → each leaves a dead tuple | "~14× dead tuples" under a pinned horizon | Append-only; never deletes a row on hot path |
| P2 | **Table & index bloat** | Dead tuples inflate heap + indexes; scans get slower | "table size ~15×", "dequeue throughput −35%" | `TRUNCATE` rotation reclaims all at once |
| P3 | **VACUUM pressure / autovacuum starvation** | Autovacuum can't keep up at high churn; falls behind | River issue #59 (autovacuum starvation) | No VACUUM needed on hot path (`n_dead_tup = 0`) |
| P4 | **xmin horizon pinning** | A long-running txn (analytics/OLAP, idle-in-txn) pins the global xmin → VACUUM **cannot** reclaim dead tuples at all → "death spiral" | PlanetScale (2026): "death spiral at 800 jobs/sec with OLAP on the side"; "worst case for SKIP LOCKED" | **Immune** — rotation doesn't rely on GC; keeps `n_dead_tup = 0` even with xmin held |
| P5 | **Lock contention / visibility delays** | `SKIP LOCKED` claims rows individually; lock churn + visibility lag under contention | (Que uses advisory locks instead, but still `DELETE`s) | Snapshot batching — no row locks at all |
| P6 | **Backlog explosions** | Producer outpaces cleanup; rows pile up | Heroku/Brandur (2015): "60k backlog in one hour" | Back-pressure: slow consumer blocks rotation, bounded |
| P7 | **Performance drift over time** | Same workload gets slower after months of accumulated bloat | "does not get slower because it has been running for months" | Steady-state by construction |

**Industry corroboration PgQue cites:** "Oban Pro shipped **table partitioning** to
mitigate it; PGMQ ships **aggressive autovacuum** settings." → i.e. *even the serious
players treat bloat as the #1 problem and partition or tune around it.*

**The trade-off PgQue accepts:** ~52 ms median latency (tick batching) and
Postgres-only, no sub-ms dispatch.

---

## Part 3 — Where sqlery sits on each problem

### 3.1 sqlery's relevant mechanics ✅ (verified)
- **Claim** (`core/claiming.py` → backend): fetch `get_claimable_jobs(limit=1)`,
  then `atomic_claim_job`. **One job per claim**, retry loop ≤10.
  - PG: `queryset.select_for_update(skip_locked=True)` + `UPDATE status='running'`.
  - SQLite: optimistic `UPDATE … SET status='running', version=version+1 WHERE
    id=? AND status='queued' AND version=?`.
- **Lifecycle**: jobs stay as rows; `status` ∈ {queued, running, success, failed,
  archived, shutting_down}. Completed jobs are **retained**, not deleted on finish.
- **Cleanup** (`core/cleanup.py` → `CleanupManager`): **`DELETE`-based**, two modes
  — age-based (**default 30 days**) and count-based (keep N newest per status) —
  plus an optional `vacuum_database()` for PG.
- **Retention knobs**: `result_ttl`, `failure_ttl` (per-job TTLs).
- **Ordering**: by `priority` (higher sooner) then `scheduled_at`.
- **No** partitioning, **no** table rotation, **no** TRUNCATE path. ✅

### 3.2 Scorecard

| Problem | sqlery exposure | Why |
|---------|-----------------|-----|
| P1 dead tuples | **Exposed** | claim = UPDATE (dead tuple); finish = UPDATE; cleanup = DELETE (dead tuple) |
| P2 table/index bloat | **Exposed** | finished rows linger up to 30 days by default; the working table holds queued+running+success+failed together |
| P3 autovacuum | **Exposed**, partially mitigated | relies on autovacuum + optional manual `vacuum_database()` |
| P4 xmin pinning | **Exposed (worst case)** | same architecture PlanetScale's death-spiral describes; nothing structural protects it |
| P5 lock contention | **Exposed**, modest | `SKIP LOCKED`, but **claims 1 row at a time** → more round-trips/contention at high worker counts |
| P6 backlog | **Exposed** | no structural back-pressure; depends on worker throughput |
| P7 drift | **Exposed** | bloat-driven drift unless cleanup + vacuum keep pace |

---

## Part 4 — "Would sqlery inevitably degrade badly?" — honest answer

**No, not *inevitably* — but it is *structurally exposed*, and under a specific
(common) combination it degrades exactly as PgQue describes.**

**It will be fine when:**
- Throughput is moderate, autovacuum is healthy and tuned for the hot table,
- There are **no long-running / idle-in-transaction** sessions pinning xmin,
- Cleanup runs regularly and retention is short,
- There's a **partial index** on pending jobs so the claim query doesn't scan
  finished rows (❓ *must confirm sqlery ships one*).

**It will degrade — potentially "death-spiral" badly — when:**
- High sustained job rate (hundreds–thousands/sec), **and**
- An OLAP query / analytics replica / long transaction pins xmin (P4), so VACUUM
  can't reclaim the UPDATE/DELETE churn → dead tuples grow (~14×), heap+index
  bloat (~15×), dequeue throughput drops (~35%), and it keeps getting worse.

So the precise framing for your README/positioning: sqlery's degradation is
**conditional, not inevitable** — but the conditions (OLAP alongside OLTP, long
transactions, bursty load) are exactly the ones real production systems hit. The
default **30-day retention** makes the baseline working set unnecessarily large,
which worsens P2/P5 well before any death spiral.

---

## Part 5 — Mitigation ladder (cheap → deep)

Ordered by effort-to-impact. Most of the bottom rungs keep sqlery's existing
API/semantics intact.

### Tier 0 — Verify & tune (days)
1. **Confirm/add a partial index**: `CREATE INDEX … ON queued_job (priority DESC,
   scheduled_at) WHERE status = 'queued'`. This is the single highest-impact,
   lowest-risk change — keeps the claim query scanning only pending rows even when
   the table is full of finished ones. ❓ *check if it exists.*
2. **Shorten default retention**: 30 days → hours/short, or document aggressive
   `result_ttl`/`failure_ttl`. Smaller hot set = less bloat exposure.
3. **Per-table autovacuum tuning** for `queued_job`: lower
   `autovacuum_vacuum_scale_factor` (e.g. 0.01), raise cost limit. Ship as docs +
   a migration. (This is "the PGMQ approach.")
4. **Run cleanup frequently** and in **batches** (bounded `DELETE … LIMIT`) to
   avoid long locks and giant dead-tuple bursts.

### Tier 1 — Reduce churn (weeks)
5. **Keep `limit=1` (one worker → one job).** This is the intended design and is
   correct: `SKIP LOCKED` already lets N workers each grab a distinct row without
   blocking, so per-worker single-job claiming is the right model. The P5
   "contention" note is *not* an argument to batch-claim — it's an argument to (a)
   keep the partial index tight so each claim is an index-only hit on a small
   pending set, and (b) cut the dead-tuple churn structurally (Tier 2), not to
   claim N at once. Batch claiming is explicitly **out of scope**.
6. **Heartbeat/zombie handling** already exists (good) — make sure requeue of dead
   workers' jobs doesn't add churn spikes.

### Tier 2 — Structural, Postgres-only (the real fix for P1–P4)
8. **Partition `queued_job` by time** and reclaim old partitions with
   **DROP/TRUNCATE** instead of `DELETE` → no dead tuples to VACUUM, immune to
   xmin pinning. This is "the Oban Pro approach" and a work-queue-shaped version
   of PgQue's rotation. Keeps current claim semantics + `limit=1`.
   **→ Full guide with SQL in [Appendix A](#appendix-a--truncate--partition-rotation-guide).**

### Tier 3 — New engine (largest)
10. A **PgQue-style append + snapshot-batch + rotation backend** for true
    zero-bloat + fan-out. Big semantic shift (see Part 6) — reserve for an explicit
    streaming/fan-out feature or the Postgres-only variant.

---

## Part 6 — A Postgres-only sqlery ("sqlery-pg")? — evaluation

You said you don't discard a separate Postgres-only build. Here's the honest case.

**What going PG-only unlocks** (things blocked today by the SQLite-compat goal):
- Use **partitioning + TRUNCATE rotation** freely (Tier 2/3) — kills P1–P4.
- Use Postgres-native features: `xid8`/snapshots, advisory locks, `LISTEN/NOTIFY`
  wake-ups (kill polling latency), `SKIP LOCKED` *with* partition pruning,
  generated columns, BRIN indexes on time, etc.
- Optional **PgQue-style fan-out** as a first-class feature.

**Cost / risk:**
- **Maintain two backends** (or a hard fork). Keep the `DatabaseBackend` seam so
  it's a third backend, not a fork, if possible.
- **Semantic mismatch with a full PgQue model**: sqlery is a *work queue* (claim
  one job, mutate its row: status/retry_count/priority/scheduled_at/TagLock mutex/
  per-job result). PgQue is a *batch event stream* (immutable append + cursor diff).
  A direct PgQue backend doesn't naturally express per-job state, priority ordering,
  or unique/mutex jobs. → A pure PgQue backend would be a *new product surface*,
  not a drop-in.
- **Latency**: PgQue's ~52 ms tick batching vs. immediate `SKIP LOCKED` claim.

**Recommended shape** (best risk/reward for "production-grade"):
> **Keep sqlery's work-queue semantics and API. Make a Postgres-only backend that
> uses partitioning + TRUNCATE rotation (Tier 2) + LISTEN/NOTIFY wake-ups +
> partial-index claim.** This gets PgQue's *bloat immunity* and low latency without
> adopting PgQue's stream model or losing the per-job features. Treat a true
> PgQue-style append/fan-out backend as a separate, optional "streaming mode" only
> if customers ask for fan-out.

i.e. **borrow PgQue's storage discipline, not its programming model.**

---

## Part 7 — Production-grade roadmap (proposed)

1. **Establish the benchmark first** (so claims are measured, not asserted):
   reproduce P1–P4 on sqlery — high job rate + a held `idle-in-transaction` session;
   track `n_dead_tup`, table/index size, dequeue throughput over time. This both
   validates "does it degrade?" and becomes the regression guard.
2. **Tier 0** (partial index, retention default, autovacuum docs, batched cleanup).
3. **Tier 1** (batch claiming, fast archival).
4. **Decide on PG-only backend** (Part 6 recommended shape) → partitioning +
   rotation + LISTEN/NOTIFY.
5. **Publish benchmarks** like PgQue does — bloat-under-load + latency — for
   credibility.
6. Re-run the Part 3 scorecard after each tier; goal: move P1–P4 from "Exposed"
   to "Mitigated/Immune".

---

## Part 8 — Findings from reading the v0.22.4 source ✅ (cloned & verified)

Resolved by cloning the `v0.22.4` tag and reading the code directly:

- [x] **Partial index on `status='queued'`? → NO. Confirmed absent.** The hot index
  on `sqlery_queued_job` is a **full composite** B-tree
  `(queue_name, status, -priority, created_at)` (`models.py:592`). Because `status`
  is just the 2nd column, a claim query can range-scan the queued slice *within a
  queue*, but the index **physically contains an entry for every job in every
  status** (success/failed/archived) until cleanup — so it bloats 1:1 with the
  table and with update churn. → **A partial `WHERE status='queued'` index is the
  confirmed #1 quick win** (smaller, bloat-resistant, stays hot).
- [x] **Claim SQL** (`db_compat.py`): PG = `select_for_update(skip_locked=True)`
  then a **version-checked** `UPDATE … status='running', version=version+1` (belt-
  and-suspenders even on PG). SQLite = optimistic version-counter UPDATE. **Any
  other DB falls back to plain `SELECT FOR UPDATE` with NO skip-locked** → would
  serialize/contend (PG/SQLite are the real targets though).
- [x] **Cleanup is a single unbounded `DELETE`, NOT batched** (`backend.py:478` &
  `:505`). `cleanup_jobs` does `query.delete()`; `cleanup_jobs_by_count` does
  `exclude(id__in=keep_ids)` where `keep_ids` is a materialized Python list (large-
  IN-list anti-pattern) then one `delete()`. → big dead-tuple bursts + long locks
  under backlog. **Add chunked `DELETE … LIMIT` / keyset batching.**
- [x] **Default retention = 30 days** (`core/cleanup.py:53-55`), registries 7 days,
  count default 10 000. → baseline working set is large by default; shorten it.
- [x] **VACUUM** is manual-only (`backend.py:540` `VACUUM ANALYZE sqlery_queued_job`
  …) via `vacuum_database()`, optionally invoked by `auto_cleanup`. No autovacuum
  tuning shipped.
- [x] **No LISTEN/NOTIFY — pure polling.** Workers sleep `WORKER_POLL_INTERVAL`
  (**default 5 s**, `worker.py:426`). → dispatch latency is **up to ~5 s** by
  default (vs PgQue's ~52 ms). A PG-only **LISTEN/NOTIFY wake-up** would cut
  latency and idle DB load dramatically — strong argument for the PG-only backend.

### Still open
- [ ] FastAPI adapter: does it change worker/claim behavior or just transport? (dir
  `fastapi_sqlery/` has its own `backend.py`/`async_backend.py` — skim next.)
- [ ] PgQue licensing (Apache-2.0) vs sqlery (MIT) if we borrow SQL ideas/code.
- [ ] Exact autovacuum settings to recommend for `sqlery_queued_job`.

### Net effect on Part 4's verdict
These confirm the exposure is **real and shipping today**, not hypothetical:
full (non-partial) hot index + unbatched DELETE cleanup + 30-day default retention
+ manual-only VACUUM + 5 s polling. None of it is *inevitable* degradation, but the
defaults actively work against bloat-resistance and latency. The Tier 0 changes
(partial index, batched cleanup, shorter retention, autovacuum tuning) are now all
**confirmed-needed**, and Tier 2 partition/TRUNCATE remains the structural fix.

## Appendix A — TRUNCATE / partition-rotation guide

### A.1 Why TRUNCATE (vs DELETE) — the mechanism
| Op | What it does | Dead tuples? | VACUUM needed? | Blocked by xmin pinning? | Lock |
|----|--------------|--------------|----------------|--------------------------|------|
| `DELETE … WHERE` | marks each row dead (still in heap until vacuumed) | **yes, one per row** | yes | **yes** — can't reclaim while horizon held | row locks + WAL per row |
| `TRUNCATE table` | swaps in a fresh empty relfilenode, drops old storage at once | **none** | **no** | **no** | `ACCESS EXCLUSIVE` on *that table only*, O(1)-ish, tiny WAL |
| `DROP TABLE partition` | removes the partition + its storage | none | no | no | brief lock on that partition |

The catch: **TRUNCATE is all-or-nothing per table** — you can't "TRUNCATE the old
rows." So to reclaim *old* work without touching *live* work, each time-slice of
jobs must be its **own table** → that's what partitioning gives you. You then
TRUNCATE/DROP whole *old* partitions, never the active one.

This is exactly how PgQue does it: it never deletes a row; it fills a child table,
then `TRUNCATE`s a child "once every consumer has read past it." The sqlery
translation: fill a time partition, then `DROP`/`TRUNCATE` it "once every job in it
is terminal **and** past retention." The invariant — *don't reclaim a partition
that still has live work* — is the same back-pressure rule (a stuck job blocks
reclamation of its partition, just like a slow consumer blocks PgQue rotation).

### A.2 The design for sqlery (PG-only backend)
Partition the jobs table by `created_at` (hourly or daily). The hot claim path is
unchanged and keeps `limit=1`; cleanup changes from "DELETE old rows + VACUUM" to
"DROP old partitions". Dead tuples from the claim/complete UPDATEs still occur, but
they live inside a partition that will later be **dropped wholesale** — so VACUUM
never has to chase them and a pinned xmin can't cause unbounded growth (it's
bounded by `retention_window × throughput`).

```sql
-- 1. Partitioned parent. Partition key MUST be in the PK/unique constraints.
CREATE TABLE job (
    id            bigint GENERATED ALWAYS AS IDENTITY,
    queue         text        NOT NULL,
    status        text        NOT NULL DEFAULT 'queued',  -- queued|running|success|failed|archived
    priority      int         NOT NULL DEFAULT 0,
    scheduled_at  timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    retry_count   int         NOT NULL DEFAULT 0,
    max_retries   int         NOT NULL DEFAULT 0,
    payload       jsonb,
    PRIMARY KEY (created_at, id)            -- created_at in PK so partitioning is legal
) PARTITION BY RANGE (created_at);

-- 2. THE hot-path index: partial, only pending rows. Defined on the parent,
--    so every partition inherits it. On a drained old partition this index has
--    zero entries → scanning it costs ~nothing.
CREATE INDEX job_claim_idx ON job (priority DESC, scheduled_at)
    WHERE status = 'queued';

-- 3. Concrete partitions (hourly here). Make them ahead of time.
CREATE TABLE job_2026_06_03_10 PARTITION OF job
    FOR VALUES FROM ('2026-06-03 10:00') TO ('2026-06-03 11:00');
CREATE TABLE job_2026_06_03_11 PARTITION OF job
    FOR VALUES FROM ('2026-06-03 11:00') TO ('2026-06-03 12:00');
-- (+ a DEFAULT partition as a safety net for out-of-range rows)
CREATE TABLE job_default PARTITION OF job DEFAULT;
```

**Claim query — unchanged semantics, `limit=1`:**
```sql
WITH cand AS (
  SELECT created_at, id
  FROM job
  WHERE status = 'queued'
    AND (scheduled_at IS NULL OR scheduled_at <= now())
  ORDER BY priority DESC, scheduled_at
  FOR UPDATE SKIP LOCKED
  LIMIT 1                                   -- one worker, one job
)
UPDATE job j
   SET status = 'running'
  FROM cand
 WHERE j.created_at = cand.created_at AND j.id = cand.id
RETURNING j.*;
```
Note: the claim filters on `status`, not `created_at`, so it touches every
partition's `job_claim_idx` — but old partitions are *drained*, so their partial
index is empty and the scan is trivial. Only recent partitions hold `queued` rows.

### A.3 The rotation/reclaim job (replaces DELETE-based cleanup)
Run from the scheduler/ticker. Two safe variants:

**Variant 1 — DROP old partitions (simplest):**
```sql
DO $$
DECLARE
  part   regclass;
  hi     timestamptz;
  live   boolean;
BEGIN
  FOR part, hi IN
    SELECT c.oid::regclass,
           -- upper bound of this partition's range:
           (regexp_match(pg_get_expr(c.relpartbound, c.oid),
                         'TO \(''(.*?)''\)'))[1]::timestamptz
    FROM pg_inherits i
    JOIN pg_class   c ON c.oid = i.inhrelid
    WHERE i.inhparent = 'job'::regclass
  LOOP
    CONTINUE WHEN hi IS NULL;                       -- skip DEFAULT
    CONTINUE WHEN hi > now() - interval '2 hours';  -- retention window
    -- INVARIANT: only reclaim if no live work remains in the partition
    EXECUTE format(
      'SELECT EXISTS (SELECT 1 FROM %s WHERE status IN (''queued'',''running''))',
      part) INTO live;
    IF live THEN CONTINUE; END IF;                  -- a stuck job blocks reclaim
    EXECUTE format('DROP TABLE %s', part);          -- storage gone, no dead tuples
  END LOOP;
END $$;
```

**Variant 2 — recycle via DETACH + TRUNCATE (PgQue-exact, no catalog churn):**
```sql
-- PG 14+: detach without a long lock, truncate off to the side, re-attach for a
-- future range. Avoids constant DROP/CREATE catalog turnover under high tick rates.
ALTER TABLE job DETACH PARTITION job_2026_06_03_10 CONCURRENTLY;
TRUNCATE job_2026_06_03_10;                          -- instant, no dead tuples
ALTER TABLE job_2026_06_03_10
  RENAME TO job_2026_06_03_18;                        -- reuse the table object
ALTER TABLE job ATTACH PARTITION job_2026_06_03_18
  FOR VALUES FROM ('2026-06-03 18:00') TO ('2026-06-03 19:00');
```
Variant 1 is easier to operate; Variant 2 matches PgQue's fixed-ring TRUNCATE and
avoids catalog bloat if you rotate very frequently. Most deployments want Variant 1
(or just let **pg_partman** do create-ahead + retention-drop for you).

### A.4 Operational notes / gotchas
- **Create partitions ahead of time** (the scheduler should always keep the next
  few hours provisioned), or rows fall into `job_default` and lose pruning benefit.
  `pg_partman` automates create-ahead + drop-behind; or do it in sqlery's scheduler.
- **DROP/TRUNCATE takes `ACCESS EXCLUSIVE`** on that partition — fine because old
  partitions have no live readers. Use `DETACH … CONCURRENTLY` first if you want to
  be extra safe under heavy mixed traffic.
- **Stuck/long-running jobs pin their partition** (can't reclaim it). That's the
  intended back-pressure, but pair it with the existing zombie/heartbeat reaper so
  dead-worker jobs get requeued (into a *current* partition) instead of wedging an
  old one forever.
- **Retention window** = how long terminal jobs (results/failures) stay queryable.
  Set it from `result_ttl`/`failure_ttl`; the partition granularity (hour/day)
  bounds the slack. Short retention + fine granularity = smallest footprint.
- **Sizing**: footprint ≈ `throughput × retention`, independent of uptime → kills
  P7 "drift over months". Pinned xmin only bloats partitions inside the retention
  window, which then get dropped → P4 becomes bounded, not a death spiral.

### A.5 Django/ORM integration caveats (because of the adapter layer)
- Django's ORM doesn't manage native declarative partitioning. Implement the
  parent + partitions + rotation in **raw SQL migrations** (`RunSQL`) inside the
  **PG-only core backend**, or use **`django-postgres-extra` (psqlextra)** which
  provides `PostgresPartitionedModel` + a `pgpartition` management command, or
  drive partition lifecycle with **pg_partman**.
- The PK must include `created_at`. **Django 5.2 added composite primary keys** —
  and sqlery already requires Django 5.2 LTS — so `PRIMARY KEY (created_at, id)`
  is expressible in the ORM now (nice alignment). Otherwise keep `id` unique-per-
  partition and treat `(created_at, id)` as the logical key.
- **FKs into `job`** (`Worker.current_job`, `JobRegistry`): FKs *referencing* a
  partitioned table are supported on modern PG, but simplest is to drop the DB-level
  FK constraint on these and keep the reference as a plain indexed `bigint`
  (the app already tracks lifecycle). Verify what sqlery's models do today.
- Keep this entirely behind the **PG-only backend** so the SQLite path (which can't
  partition) is unaffected — it keeps DELETE-based cleanup.

### A.6 Migration path (incremental, low-risk)
1. Ship the **partial index** first (Tier 0) — biggest single win, zero schema risk.
2. Add the partitioned `job` table as an **opt-in PG-only backend**; dual-write or
   cut over via a maintenance window; keep old table until drained.
3. Replace `CleanupManager`'s DELETE path with **partition DROP** for the PG backend
   (SQLite backend keeps DELETE).
4. Add **create-ahead + drop-behind** to the scheduler (or adopt pg_partman).
5. Gate behind the bloat **benchmark** from Part 7 so the win is measured.

---

## Appendix B — Variant 1: the specific changes to sqlery

Concrete, file-by-file change-set to convert sqlery's `sqlery_queued_job` to a
**time-range-partitioned table reclaimed by DROP** (Variant 1), grounded in the
v0.22.4 source. **PG-only and opt-in**; SQLite / non-partitioned PG keep today's
DELETE path untouched.

> **Critical design rule:** do **not** use blind `pg_partman` *retention* to drop
> partitions. A job created at `T` but `scheduled_at = T+30d`, a stuck `running`
> job, or a retrying `failed` job would still live in an old partition; time-based
> drop would delete live work. Use pg_partman (or a sqlery task) only for
> **create-ahead**, and drop with an **invariant check: a partition is reclaimable
> only when it has zero `queued`/`running` rows AND is past retention.** This is the
> same "don't rotate past a live consumer" rule PgQue enforces; here a stuck/
> long-delayed job legitimately pins its partition (back-pressure, by design).

### B.0 Change manifest — files to touch

> Heads-up: sqlery has **two parallel backend stacks** — the **Django ORM** backend
> *and* a **FastAPI/SQLAlchemy** backend (`fastapi_sqlery/`), each with **sync +
> async** variants and its own settings. Every backend-level change below must be
> made in **both** stacks or the partitioned mode only works under one adapter.

| # | File | New? | Change |
|---|------|------|--------|
| 1 | `src/sqlery/core/partitioning.py` | **new** | `ensure_future_partitions()`, `reclaim_drained_partitions()` (invariant-checked DROP), `_list_partitions()`/bound-parsing helpers. Pure SQL via a passed-in cursor. |
| 2 | `src/sqlery/core/cleanup.py` | edit | `CleanupManager.auto_cleanup`/`cleanup_old_jobs`: when partitioned-PG, call partition maintenance instead of the DELETE path for jobs (registries unchanged). |
| 3 | `src/sqlery/core/daemon.py` (~L383) | edit | Run create-ahead + reclaim on a **fast cadence**. Current `CLEANUP_INTERVAL_HOURS=24` is **too coarse** to provision hourly partitions — add a separate `PARTITION_MAINTENANCE_INTERVAL_MINUTES` tick (default ~5 min). |
| 4 | `src/sqlery/core/cli.py` (~L372) | edit | Add a `partitions` action (standalone) calling the new maintenance fns. |
| 5 | `src/sqlery/django_sqlery/models.py` (L344, L592) | edit | Composite PK `(created_at,id)`; add **partial pending index** `WHERE status='queued'`; drop redundant full composite + `created_desc` indexes. |
| 6 | `src/sqlery/django_sqlery/models.py` (`JobRegistry.job`, `Worker.current_job`) | edit | FKs → plain indexed `BigIntegerField` (composite PK breaks single-col FKs). |
| 7 | `src/sqlery/django_sqlery/migrations/0028_partition_queued_job.py` | **new** | PG-only `RunSQL` (`atomic=False`): rename→partitioned parent→default partition→partial index→copy→drop legacy, with `state_operations` matching #5/#6. SQLite/other vendor = no-op. |
| 8 | `src/sqlery/django_sqlery/backend.py` (L455/L482/L529) | edit | `_partitioned_pg()` helper; `cleanup_jobs`/`cleanup_jobs_by_count` short-circuit to partition-drop; `vacuum_database` skips the partitioned table. |
| 9 | `src/sqlery/django_sqlery/async_backend.py` | edit | Mirror #8 (async). |
| 10 | `src/sqlery/django_sqlery/db_compat.py` (L99–110, L137–150) | edit | Add `created_at` to the claim status-flip `UPDATE` filter so PG prunes to one partition. |
| 11 | `src/sqlery/django_sqlery/settings.py` (`DEFAULTS`, L8) | edit | New config keys (see B.7) + `PARTITION_MAINTENANCE_INTERVAL_MINUTES`. |
| 12 | `src/sqlery/fastapi_sqlery/backend.py` (L393/L433/L523) | edit | Mirror #8 for the SQLAlchemy backend. |
| 13 | `src/sqlery/fastapi_sqlery/async_backend.py` | edit | Mirror #8 (async SQLAlchemy). |
| 14 | `src/sqlery/fastapi_sqlery/database.py` | edit | Emit partitioned DDL + partial index + create-ahead instead of plain `create_all` for the jobs table (or an Alembic migration). |
| 15 | `src/sqlery/fastapi_sqlery/config.py` | edit | Mirror the new config keys (#11). |
| 16 | `src/sqlery/management/commands/cleanup_jobs.py` **and** `src/sqlery/django_sqlery/management/commands/cleanup_jobs.py` | edit | Add a `partitions` action to the `choices` + handler. |
| 17 | `docs/` + `tests/` | edit | Document `SQLERY_PG_PARTITIONED`; add tests: drop-skips-live-partition, far-future-scheduled pin, claim-after-partition, migration round-trip. |
| 18 | `alembic/` (root) | edit | If the SQLAlchemy/Alembic path is used for schema, add the partition migration mirroring #7. |

Minimum viable subset (ship first): **#5 partial index only** — one model edit + one
trivial migration, no partitioning, immediate bloat relief, zero risk.

Full Variant 1, Django-only first: **#1–11 + #16–17** (defer FastAPI #12–15 until the
Django path is proven).

### B.1 Model — composite PK (`django_sqlery/models.py`, `QueuedJob` ~L344)
The partition key (`created_at`, currently `auto_now_add` at L565) must be in the
PK. Django 5.2 (already required) supports composite PKs:
```python
class QueuedJob(models.Model):
    # add:
    pk = models.CompositePrimaryKey("created_at", "id")
    # keep id as an explicit autofield bound to the existing sequence so ids stay
    # globally unique across partitions (claim filters by id alone):
    id = models.BigAutoField(primary_key=False)
    ...
    class Meta:
        db_table = "sqlery_queued_job"
        indexes = [
            # NEW hot-path partial index (the #1 win), replaces reliance on the
            # full composite for claiming:
            models.Index(
                fields=["queue_name", "-priority", "created_at"],
                name="sqlery_job_pending_idx",
                condition=Q(status="queued"),
            ),
            models.Index(fields=["task_path", "status"]),
            models.Index(fields=["-finished_at"], name="sqlery_job_finished_desc"),
            # drop the now-redundant full (queue,status,prio,created) + created_desc
        ]
```
**FK fallout:** `JobRegistry.job` and `Worker.current_job` are FKs to
`QueuedJob`. A composite PK breaks single-column FKs. Change those two to plain
indexed `BigIntegerField` (`job_id` / `current_job_id`) — app already tracks
lifecycle, no DB-level FK needed. (`parent_job_id` at L394 is already a plain int.)

### B.2 Schema migration (`migrations/0028_partition_queued_job.py`, PG-only)
`atomic = False` (because `CREATE INDEX CONCURRENTLY` / large copy). Guard on
`connection.vendor == "postgresql"`; on SQLite/others make it a no-op. Skeleton SQL
(generate the exact column list from the model — `LIKE … INCLUDING CONSTRAINTS`
copies the old single-col PK which is invalid for partitioning, so set the PK
explicitly):
```sql
ALTER TABLE sqlery_queued_job RENAME TO sqlery_queued_job_legacy;

CREATE TABLE sqlery_queued_job (LIKE sqlery_queued_job_legacy
        INCLUDING DEFAULTS INCLUDING IDENTITY INCLUDING STORAGE)
    PARTITION BY RANGE (created_at);
ALTER TABLE sqlery_queued_job
    ADD PRIMARY KEY (created_at, id);                       -- partition key in PK
ALTER TABLE sqlery_queued_job ALTER COLUMN id
    SET DEFAULT nextval('sqlery_queued_job_id_seq');        -- reuse global sequence

-- default partition catches anything outside provisioned ranges
CREATE TABLE sqlery_queued_job_default PARTITION OF sqlery_queued_job DEFAULT;

-- partial hot-path index on the parent → inherited by all partitions
CREATE INDEX sqlery_job_pending_idx ON sqlery_queued_job
    (queue_name, priority DESC, created_at) WHERE status = 'queued';

-- copy existing rows (creates partitions via default; or pre-create then copy)
INSERT INTO sqlery_queued_job SELECT * FROM sqlery_queued_job_legacy;
DROP TABLE sqlery_queued_job_legacy;
```
Wrap with `migrations.RunSQL(sql, reverse_sql, state_operations=[…])` so Django's
migration state matches the model changes in B.1.

### B.3 Partition maintenance module (new: `core/partitioning.py`)
Framework-agnostic (raw SQL via the backend), two functions:
```python
INTERVAL  = get_config("SQLERY_PARTITION_INTERVAL", "1 hour")
PREMAKE   = get_config("SQLERY_PARTITION_PREMAKE", 6)       # provision N ahead
RETENTION = get_config("SQLERY_PARTITION_RETENTION", "2 hours")

def ensure_future_partitions(cur):
    """Create-ahead: make sure the next PREMAKE intervals exist.
    (Or delegate to pg_partman create_parent + run_maintenance — see B.6.)"""
    # CREATE TABLE IF NOT EXISTS sqlery_queued_job_<ts>
    #     PARTITION OF sqlery_queued_job FOR VALUES FROM (lo) TO (hi);

def reclaim_drained_partitions(cur):
    """Invariant-checked DROP (NOT blind time retention)."""
    for part, upper_bound in _list_partitions(cur, "sqlery_queued_job"):
        if upper_bound is None:                 # skip DEFAULT
            continue
        if upper_bound > now() - RETENTION:      # still inside retention window
            continue
        cur.execute(f"SELECT EXISTS(SELECT 1 FROM {part} "
                    f"WHERE status IN ('queued','running'))")
        if cur.fetchone()[0]:                    # live work → pin partition
            continue
        cur.execute(f"DROP TABLE {part}")        # storage gone, zero dead tuples
```

### B.4 Route cleanup to partition-drop (`django_sqlery/backend.py`)
In `cleanup_jobs` (L455) and `auto_cleanup`, short-circuit when partitioned-PG:
```python
def cleanup_jobs(self, ...):
    if self._partitioned_pg():        # SQLERY_PG_PARTITIONED and vendor == postgresql
        with connection.cursor() as cur:
            ensure_future_partitions(cur)
            reclaim_drained_partitions(cur)
        return {"mode": "partition-drop"}
    # ...existing DELETE path for SQLite / legacy PG unchanged...
```
`vacuum_database()` (L529) becomes a **no-op for the partitioned table** — DROP
leaves nothing to VACUUM (keep VACUUM for the other small tables if desired).

### B.5 Wire-in points (already exist — just call the new path)
- **Daemon loop**: `core/daemon.py:383` already gates on `AUTO_CLEANUP_JOBS`; it
  now calls the partition-aware `cleanup_jobs`, so create-ahead + drop run on the
  existing maintenance cadence. Ensure cadence ≤ partition interval so partitions
  are always provisioned before rows need them.
- **CLI / mgmt command**: `core/cli.py:372` and
  `management/commands/cleanup_jobs.py` already invoke `CleanupManager`; add a
  `partitions` action that calls `ensure_future_partitions` /
  `reclaim_drained_partitions` for manual/cron use.

### B.6 Optional: let pg_partman do create-ahead only
```sql
SELECT partman.create_parent(
  p_parent_table := 'public.sqlery_queued_job',
  p_control := 'created_at', p_type := 'range', p_interval := '1 hour',
  p_premake := 6);
UPDATE partman.part_config
   SET retention = NULL                 -- IMPORTANT: disable pg_partman retention
 WHERE parent_table = 'public.sqlery_queued_job';
-- schedule create-ahead only:
SELECT cron.schedule('sqlery_partman', '*/5 * * * *',
  $$CALL partman.run_maintenance_proc()$$);
```
Then sqlery only owns `reclaim_drained_partitions` (the invariant-checked DROP).

### B.7 Config keys (settings / `get_config`)
| key | default | meaning |
|-----|---------|---------|
| `SQLERY_PG_PARTITIONED` | `False` | opt into partitioned PG backend |
| `SQLERY_PARTITION_INTERVAL` | `"1 hour"` | partition width |
| `SQLERY_PARTITION_PREMAKE` | `6` | how many future partitions to keep ready |
| `SQLERY_PARTITION_RETENTION` | `"2 hours"` | min age before a *drained* partition may drop |

### B.8 Claim path — almost unchanged, one tweak
`db_compat.py` claim/update still works (`id` stays globally unique). Minor: the
status-flip `UPDATE` filters by `id` only; under partitioning that scans all
partitions to find the row. Add `created_at` to the update filter (the claimed row
object has it) so PG prunes to one partition:
`QueuedJob.objects.filter(id=job.id, created_at=job.created_at, version=…).update(…)`.

### B.9 Caveats to document for operators
- A **far-future `scheduled_at`** job (status stays `queued`) **pins its partition**
  until it runs — intended, but a job scheduled months out blocks reclaim of a
  whole old partition. If you support long delays, route them to a separate small
  "scheduled" table and only insert into the partitioned hot table when due.
- A **stuck `running`** job pins its partition until the existing zombie/heartbeat
  reaper requeues it (into a *current* partition) — good synergy; make sure the
  reaper runs.
- DROP takes a brief `ACCESS EXCLUSIVE` on the (drained, reader-less) partition;
  use `ALTER TABLE … DETACH PARTITION … CONCURRENTLY` before DROP if you want zero
  chance of blocking under heavy mixed traffic.
- Footprint becomes ≈ `throughput × retention` regardless of uptime (kills P7);
  pinned-xmin bloat is bounded to in-window partitions that then drop (P4 bounded).

### B.10 Ship order
1. **B.1 partial index alone** (even without partitioning) — biggest single win,
   shippable immediately, zero risk.
2. B.2 migration + B.3/B.4/B.5 behind `SQLERY_PG_PARTITIONED=False` default.
3. Flip on in staging; validate with the Part 7 bloat benchmark; document defaults.

---

## Appendix C — `sql/pgwq.sql`: PgQue-derived work queue (Variant 1 + limit=1)

A standalone, pure-SQL deliverable: **`sql/pgwq.sql`** — a PgQue-derived
("PgQue-WQ") zero-bloat **work queue** that keeps PgQue's storage discipline but
uses a `limit=1` claim instead of snapshot-batch delivery. Apache-2.0 derivative
(PgQue → PgQ lineage credited in the file header).

**What it keeps from PgQue:** zero-bloat by **table rotation** — the `pgwq.job`
table is RANGE-partitioned by `created_at`; maintenance creates partitions ahead
and **DROPs drained old ones** (Variant 1). Dead tuples from claim/complete
UPDATEs die *with* the partition → no VACUUM chase, xmin-pinning bounded.

**What it changes (the ask):** delivery is a classic work-queue **single-job
claim** — `SELECT … FOR UPDATE SKIP LOCKED … LIMIT 1` + status flip — i.e. one
worker → one job, matching sqlery's model (not PgQue's batch/cursor stream).

**Invariant (same as PgQue's rotation rule):** never reclaim a partition with live
`queued`/`running` rows. A stuck/long-delayed job pins its partition (back-pressure);
`requeue_stalled()` recovers dead-worker jobs so they don't pin forever.

**API:** `enqueue · claim · complete · fail (exp-backoff retry → dead) ·
requeue_stalled · ensure_partitions · reclaim_partitions · maint · dlq_inspect ·
dlq_replay · stats`. Config table with `partition_interval / premake / retention /
visibility_timeout`. Schedule `SELECT pgwq.maint()` every few minutes (pg_cron).

**Verified on PostgreSQL 16** (loaded + smoke-tested in this session):
- priority ordering + future-`scheduled_at` skip + `limit=1` claim ✅
- complete / fail→dead / DLQ inspect+replay ✅
- **rotation invariant**: partition with a `queued` row is **not** dropped; once
  drained it **is** dropped ✅
- retry exp-backoff (`scheduled_at` pushed to future, `retry_count++`) ✅
- stalled-worker requeue ✅
- `EXPLAIN` confirms the claim uses the **partial `WHERE state='queued'` index**
  (Merge Append across partitions, `SKIP LOCKED LIMIT 1`) ✅

**Relation to the sqlery plan:** pgwq is the *engine-level reference* for Appendix
B — it demonstrates, in isolation and against a live PG, the exact storage model
(partition + invariant-checked DROP) that Appendix B grafts onto sqlery's tables
while preserving sqlery's Python API and `limit=1` claim. It's also a ready basis
for a Postgres-only "sqlery-pg" backend (Part 6).

---

## Appendix D — PgQue Python SDK compatibility with `pgwq`

**Verdict: not compatible.** PgQue's Python SDK (`clients/python/pgque/`) is a
**streaming/pub-sub** client; `pgwq` is a **work queue**. The mismatch is at both
the SQL-call level and the data-model level — it would fail on the first call.

### D.1 Why (evidence from the SDK source)
The SDK calls these SQL functions (`client.py`, `consumer.py`); none exist in
`pgwq`, and the concepts don't map:

| SDK SQL call | in pgwq? | mismatch |
|---|---|---|
| `pgque.send` / `send_batch` | ✗ | nearest is `pgwq.enqueue` |
| `pgque.subscribe`/`unsubscribe` | ✗ | pgwq has no consumer registry |
| `pgque.receive` → **batch** + `batch_id` | ✗ | pgwq claims **one job** (`limit=1`), no batches |
| `pgque.ack(batch_id)` / `nack(batch_id,msg)` | ✗ | pgwq has per-job `complete`/`fail`, no cursor |
| `pgque.ticker` / `force_next_tick` | ✗ | pgwq uses partition maintenance, no ticks |
| `receive_coop`, subconsumers | ✗ | no cooperative fan-out |

Data model: `types.Message(msg_id, batch_id, type, payload, retry_count,
created_at, ev_extra1..4)` ↔ `pgque.message` composite. A pgwq job is
`(id, created_at, queue, state, priority, scheduled_at, payload, retry_count,
max_retries, last_error, locked_by, locked_at, finished_at)` — no `batch_id`,
no `ev_extra*`. `Consumer` is a LISTEN/NOTIFY loop around `receive`/`ack` with
`@on(event_type)` routing; pgwq has no NOTIFY and no batch ack.

**What *is* reusable:** the SDK *scaffolding* — `psycopg` connection ownership,
`connect()`, context-manager lifecycle, JSON payload encoding, the exception
hierarchy, and the LISTEN/NOTIFY wait loop mechanics. The *verbs* are not.

### D.2 Option A (recommended) — fork the SDK into a `pgwq` work-queue client
Keep the scaffolding, replace the verbs. File-by-file (under a new
`clients/python/pgwq/`, derived from `clients/python/pgque/`):

| File | Change |
|---|---|
| `client.py` | Keep `connect`, ctx-manager, JSON encode, `_wrap_sql_error`. **Replace methods:** `send`→`enqueue(queue,payload,priority=0,scheduled_at=None,max_retries=0)` → `pgwq.enqueue`. **Add** `claim(queue,worker)`→`select * from pgwq.claim(%s,%s)` (returns `Job` or `None`), `complete(created_at,id)`, `fail(created_at,id,error=None)`, `maint()`, `dlq_inspect/dlq_replay`, `stats`. **Delete** `subscribe/unsubscribe/receive/ack/nack/ticker/force_next_tick/*coop*`. |
| `types.py` | Replace `Message`/`Event` with a `Job` dataclass mirroring `pgwq.job` columns (above). |
| `consumer.py` | Rewrite `Consumer`→`Worker`: loop = `claim` (limit=1) → dispatch by routing key (e.g. `payload["task"]`) via `@worker.task(name)` → `complete` on success / `fail` on exception. Keep the LISTEN/NOTIFY wakeup, but on channel `pgwq_<queue>` (needs the SQL change in D.3); fall back to `poll_interval`. Drop batch/ack semantics. |
| `errors.py` | Trim to `PgwqError`, `PgwqConnectionError`. Drop `Batch/Consumer/QueueNotFound` (no such concepts). |
| `__init__.py` | Export `PgwqClient`, `Worker`, `Job`, `connect`, errors. |
| `pyproject.toml` | Rename package `pgque`→`pgwq`; keep `psycopg` dep. |
| `README.md`, `tests/` | Rewrite for the work-queue API (enqueue/claim/complete/fail; worker loop; rotation is server-side via `maint()`). |

### D.3 One supporting SQL change (for the event-driven Worker)
`sql/pgwq.sql` → in `pgwq.enqueue`, after the INSERT add:
```sql
perform pg_notify('pgwq_' || p_queue, '1');
```
so a waiting `Worker` wakes immediately instead of waiting out `poll_interval`.
(Optional — without it the Worker still works by polling. Note: NOTIFY fires on
commit, matching the worker's next claim.)

Net for Option A: **~5 SDK files rewritten + 1 one-line SQL addition.** No change
to pgwq's model; preserves `limit=1`.

### D.4 Option B (not recommended) — SQL shims so the stock SDK runs unchanged
Add a `pgque`-named compatibility layer to `sql/pgwq.sql`: a `pgque.message`
composite type, a consumers/pseudo-batch registry, and functions `send`→enqueue,
`subscribe/unsubscribe`→registry no-ops, `receive(q,c,max)`→claim up to `max` rows
as a synthetic batch (set `max=1` to emulate `limit=1`), `ack(batch_id)`→complete
that pseudo-batch, `nack`→fail, `ticker/force_next_tick`→no-ops. **Downsides:** you
re-introduce batch/cursor concepts pgwq deliberately dropped, must fake `batch_id`
and `ev_extra*`, and the SDK's `receive`+`ack` batch model fights `limit=1`. Only
worth it if you must run *unmodified* PgQue consumer code. **Prefer Option A.**

### D.5 Recommendation — DONE (Option A implemented & verified)
Built the small derived `pgwq` Python client under **`clients/python/`**:
- `pgwq/client.py` — `enqueue / claim (limit=1) / complete / fail / maint /
  requeue_stalled / stats / dlq_inspect / dlq_replay`.
- `pgwq/worker.py` — `Worker` with `@task(name)` routing, single-job claim loop,
  `complete`/`fail` per job, and LISTEN/NOTIFY wakeup on `pgwq_<queue>` with
  `poll_interval` fallback.
- `pgwq/types.py` (`Job`), `pgwq/errors.py`, `pgwq/__init__.py`, `pyproject.toml`,
  `README.md`.
- SQL (D.3): added `pg_notify('pgwq_'||queue, id)` to `pgwq.enqueue` for
  immediate worker wakeups.

**Verified on PostgreSQL 16 (psycopg 3.3.4)** end-to-end: NOTIFY-driven wakeup;
priority-ordered **`limit=1`** claiming (prio-10 job before prio-0); complete on
success; exception → `fail` → dead-letter at `max_retries=0`; `dlq_inspect` shows
the error; `dlq_replay` re-queues. It mirrors the SDK's ergonomics (psycopg,
dataclass, decorator handler) and is the natural client for both `pgwq.sql` and a
future Postgres-only "sqlery-pg" backend.

---

## Sources (verified files)
- PgQue: `README.md`, `docs/concepts.md`, `docs/latency-and-tuning.md`,
  `sql/pgque.sql` — github.com/NikolayS/PgQue (v0.2.0, Apache-2.0).
- sqlery: `src/sqlery/core/claiming.py`, `core/cleanup.py`,
  `django_sqlery/db_compat.py`, `django_sqlery/models.py`, `CHANGELOG.md` —
  github.com/intrepid-g/sqlery (v0.22.4, MIT). Architecture: `core/` +
  `django_sqlery/` + `fastapi_sqlery/`.
- Captured 2026-06-03. ❓ items not yet confirmed against source.
</content>
