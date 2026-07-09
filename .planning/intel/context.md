# Context — partition-bloat-elimination milestone

## Milestone definition

- **Name:** `partition-bloat-elimination`
- **Goal:** Eliminate the two unbounded-bloat failure modes in sqlery's PostgreSQL
  backend — xmin-pinning VACUUM starvation and full-index bloat — by moving the jobs
  table to daily time-range partitions dropped wholesale when drained. Bloat becomes
  bounded by `throughput × retention` regardless of uptime.
- **Done means:** all phase success criteria pass; PLAN.md Step 13 test matrix green under
  both SQLite and PG; a fresh PG install partitions by default; an upgraded install
  survives the Step 8 migration round-trip on a production-sized snapshot, with
  documented rollback.
- source: .planning/intel/ingest-src/GSD-CONTEXT.md (Milestone definition)

## Project state at ingest (merge mode)

- v0.23.0 (Worker-Elected Cron Scheduler) shipped 2026-06-08; no active milestone
  (STATE.md: "Awaiting next milestone"). Phases 1–11 used across v0.21–v0.23; this
  milestone introduces its own 7-phase breakdown.
- PROJECT.md names the drop-in compatibility milestone as the strongest next candidate;
  this ingest sequences partition-bloat-elimination instead (see INGEST-CONFLICTS.md INFO).
- source: /Users/gabriel/Documents/GitHub/sqlery-public/.planning/STATE.md
- source: /Users/gabriel/Documents/GitHub/sqlery-public/.planning/PROJECT.md

## Phase breakdown (ordering LOCKED — decision D10)

### Phase 1 — Quick wins (PLAN Steps 1–2) — R1, R2
- Depends on: nothing. Independently shippable.
- Partial pending index (migration 0028, concurrent ops, atomic=False); batched DELETE
  cleanup in both backends.
- Success: EXPLAIN shows new index on claim query; old index gone; 100k-row cleanup never
  holds a lock > 1 s, never deletes a mid-loop-claimed row; SQLite untouched.

### Phase 2 — Partition core (PLAN Steps 3–5) — R3, R4, R8, R9
- Depends on: nothing (pure new code + daemon wiring; activates only when table is partitioned).
- `core/partitioning.py` (ensure/reclaim/check_default/_list, raw cursor); cleanup routing
  on `partitioned_pg=True`; daemon maintenance tick with advisory locks.
- Success: four reclaim skip-rules unit-tested incl. back-pressure invariant; two
  concurrent daemons → zero DDL errors; DEFAULT-row alert; matches `sql/pgwq.sql` behavior.

### Phase 3 — Scheduled-job staging (PLAN Step 6) — R5
- Depends on: Phase 2 (daemon tick hosts the promotion loop).
- `ScheduledJob` model + migration 0030; enqueue routing at threshold; exactly-once
  promotion (`DELETE … FOR UPDATE SKIP LOCKED RETURNING` → `INSERT`); dual-table API
  surface in both adapters; shared id sequence.
- Success: 60-day-out job invisible to claims, visible to status/cancel, promoted within
  one tick of `scheduled_at - lookahead`; no double-promotion under concurrent daemons;
  config validation rejects retention ≤ threshold.

### Phase 4 — Schema cutover (PLAN Steps 7–8) — R6 (schema half), R7
- Depends on: Phases 2–3. HIGHEST-RISK phase; verification gates everything after it.
- Blast-radius audit (`.pk`, `pk=`, `pk__in`, `refresh_from_db`, `in_bulk`, FK traversals;
  known hit: `save_meta` models.py:823); composite PK `(created_at, id)`; FK demotion;
  migration 0029 exactly as PLAN Step 8 (rev 2026-06-10).
- Success: round-trip on ≥1M-row snapshot; zero rows in DEFAULT post-migration; identity
  continues from max(id)+1; clean re-run after injected mid-migration failure; `.pk`
  audit zero unaddressed hits.

### Phase 5 — Backend wiring + claim-path pruning (PLAN Steps 9–10) — R3, R6
- Depends on: Phase 4.
- `_partitioned_pg()` helper; cleanup → reclaim; vacuum skips partitioned table; mirror in
  `async_backend.py`; `created_at` added to all 11 id-only write paths (PLAN Step 10
  checklist); no `.only()`/`values()` trims `created_at`.
- Success: EXPLAIN shows single-partition pruning per checklist item; full lifecycle test
  on partitioned table; SQLite divergence matrix green.

### Phase 6 — FastAPI parity (PLAN Step 11) — R1–R6 for the SQLAlchemy backend
- Depends on: Phase 5.
- Mirror Phases 4–5 in `fastapi_sqlery/`: `database.py` partitioned DDL + partial index for
  new installs; `config.py` partition keys; sync + async backend routing.
- Success: Phase-5 lifecycle test passes against FastAPI backend; fresh install via
  `database.py` is partitioned by default.

### Phase 7 — LISTEN/NOTIFY (PLAN Step 12) — optional, no requirement
- Depends on: Phase 6. Skippable without affecting milestone "done" (decision D10).
- Opt-in `SQLERY_PG_NOTIFY`; `pg_notify` after enqueue; worker LISTEN loop + poll fallback.
- Success: flag on → dispatch latency < 100 ms in test; flag off (default) → byte-identical
  behavior.

> PLAN.md Step 13 (tests/rollback/observability) is NOT a phase — its items are
> distributed into the phase success criteria above and must be implemented inside each phase.

source: .planning/intel/ingest-src/GSD-CONTEXT.md (Phase breakdown)
source: .planning/intel/ingest-src/PLAN.md (Steps 1–13, Ship order)

## Reference artifacts (read-only, outside this repo)

All under `/Users/gabriel/Documents/GitHub/empty/sqlery-pgque/`:

- `PLAN.md` — authoritative 13-step technical spec (senior review, 14 findings, all
  folded in; status ADDRESSED, rev 2026-06-10). Ingested copy:
  `.planning/intel/ingest-src/PLAN.md`.
- `sqlery-vs-pgque.md` — full analysis (PgQue mechanics, sqlery P1–P7 exposure scorecard,
  Appendix A/B partition design, Appendix C/D pgwq). Ingested copy:
  `.planning/intel/ingest-src/sqlery-vs-pgque.md`.
- `sql/pgwq.sql` — verified pure-SQL reference: partitioned queue with invariant-checked
  rotation (PG 16). Reference behavior target for Phase 2.
- `clients/python/pgwq/` — reference Python client; `worker.py` is the LISTEN/NOTIFY loop
  reference for Phase 7.

Key sqlery-public source files: `src/sqlery/django_sqlery/{models.py,backend.py,
async_backend.py,db_compat.py}`, `src/sqlery/fastapi_sqlery/{backend.py,async_backend.py,
database.py,config.py}`, `src/sqlery/core/{cleanup.py,daemon.py}`. Latest Django
migration: `0027_*` (verified); new: 0028/0029/0030.

source: .planning/intel/ingest-src/GSD-CONTEXT.md (Target repo and working setup)

## Background analysis (DOC — sqlery-vs-pgque.md, supporting rationale only)

- PgQue thesis: the standard SKIP LOCKED + UPDATE/DELETE queue pattern degrades under
  sustained load via seven mechanisms (P1 dead tuples, P2 table/index bloat, P3 autovacuum
  starvation, P4 xmin-horizon pinning "death spiral", P5 lock contention, P6 backlog,
  P7 drift). sqlery v0.22.4 scored Exposed on all seven; P4 is worst-case.
- Source-verified findings (v0.22.4): no partial index (full composite at models.py:592);
  unbounded DELETE cleanup (backend.py); default 30-day retention; manual-only VACUUM;
  pure 5 s polling, no LISTEN/NOTIFY.
- Verdict: degradation is conditional, not inevitable — but the trigger conditions (OLAP
  beside OLTP, long transactions, bursty load) are common in production. Partition +
  invariant-checked DROP is "the Oban Pro approach" / a work-queue-shaped PgQue rotation.
- Strategy: borrow PgQue's storage discipline, not its programming model. Keep work-queue
  semantics, `limit=1` claims, per-job state.
- Open items flagged ❓ in the DOC: PgQue licensing (Apache-2.0 vs sqlery MIT) if SQL
  ideas/code are borrowed; recommended autovacuum settings (out of milestone scope);
  FastAPI adapter behavior (resolved — Phase 6 covers it).
- NOTE: the DOC's Appendix A/B parameters (hourly partitions, 2-hour retention,
  `SQLERY_PG_PARTITIONED` opt-in flag, 0028 partition migration, `INCLUDING IDENTITY` SQL,
  `scheduled_at` index column, optional pg_partman) are SUPERSEDED by GSD-CONTEXT/PLAN —
  see INGEST-CONFLICTS.md INFO bucket. Use the DOC for rationale only, never for literals.

source: .planning/intel/ingest-src/sqlery-vs-pgque.md (Parts 1–8, Appendices A–D)
