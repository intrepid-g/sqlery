---
gsd_state_version: 1.0
milestone: v0.24.0
milestone_name: partition-bloat-elimination
status: executing
stopped_at: Roadmap created for v0.24.0 (Phases 12–18); requirements traceability filled; phase context files written from ingest
last_updated: "2026-06-11T21:07:37.180Z"
last_activity: 2026-06-11 -- Phase 14 execution started
progress:
  total_phases: 7
  completed_phases: 2
  total_plans: 9
  completed_plans: 6
  percent: 67
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-06-10)

**Core value:** Every execution mode works reliably and is tested in CI across both Django and standalone integrations, on both SQLite and PostgreSQL, with operational guidance that maintainers can trust in production.
**Current focus:** Phase 14 — scheduled-job-staging

## Current Position

Phase: 14 (scheduled-job-staging) — EXECUTING
Plan: 1 of 3
Status: Executing Phase 14
Last activity: 2026-06-11 -- Phase 14 execution started

Progress: [░░░░░░░░░░] 0% (0/7 phases)

## Performance Metrics

**Velocity:**

- Total plans completed: 0
- Average duration: --
- Total execution time: 0 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| - | - | - | - |

**Recent Trend:**

- Last 5 plans: --
- Trend: --

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work (all LOCKED via doc ingest 2026-06-10 — do not re-ask):

- D1–D10 ingested from GSD-CONTEXT.md/PLAN.md: daily RANGE partitions on `created_at` with fixed defaults; hand-rolled maintenance (no pg_partman); stop-the-world migration 0029; FK demotion to `BigIntegerField`; failed-job history destroyed by default (archive hook opt-in); SQLite keeps the batched DELETE path forever; verified literals (status `'queued'`, ordering `-priority, created_at`, index trailing column `created_at`); partitioning default-on for PG (only LISTEN/NOTIFY flagged); `pg_try_advisory_lock` per maintenance function; phase ordering fixed (only Phase 18 droppable)
- Python floor raised to 3.13 (user resolution of the ingest warning) — ships in Phase 12 as R11

### Pending Todos

None yet.

### Blockers/Concerns

- MIGRATION RENUMBER DECISION (2026-06-11): The ingest's "0029=cutover / 0030=staging" labels were renumbered to match execution order. Phase 14 staging = `0029_scheduled_job_staging` (depends on 0028). Phase 15 cutover = `0030_partition_queued_job` (depends on 0029). Chain: 0028 → 0029(staging) → 0030(cutover). All references to "migration 0029 (cutover)" / "migration 0030 (staging)" in older docs now mean the SWAPPED numbers.
- Phase 15 (schema-cutover) is the HIGHEST-RISK phase; its verification gates Phases 16–18. The cutover migration (now `0030_partition_queued_job`) is stop-the-world with a rename-based rollback — the ≥1M-row round-trip test is mandatory before proceeding.
- Index DDL byte-identity invariant: the 0028 partial index and the cutover-migration DDL (now 0030) must stay identical (`queue_name, priority DESC, created_at WHERE status='queued'`) or Phase 15 fails on a name collision.
- PHASE 16 CARRY-FORWARD (from Phase 14 review CR-03): The promotion loop is now a structurally-independent daemon step, but `promote_due_scheduled_jobs` uses PG-only SQL (CTE + FOR UPDATE SKIP LOCKED + pg advisory locks) and is gated on `_partition_maint_available`. Two items for Phase 16: (1) wire `backend.get_raw_cursor()` (currently a TODO/AttributeError-guarded no-op) so promotion actually runs; (2) decide SQLite far-future staging — `create_job` routes far-future jobs to `sqlery_scheduled_job` on ALL backends incl. SQLite, but the PG-only promotion cannot drain them on SQLite. Either gate staging routing on `_partitioned_pg()` (so SQLite keeps far-future jobs in the queue) or provide a SQLite-compatible promotion path. Tests/CONTEXT currently keep staging backend-agnostic.

## Deferred Items

Items acknowledged and carried forward from previous milestone close:

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| Feature | Worker takeover of scheduling even when a daemon is up | Deferred | 2026-06-08 |
| Config | `WORKER_SCHEDULER_ELIGIBLE` opt-out knob (default always-eligible) | Deferred | 2026-06-08 |

Items acknowledged at v0.23.0 milestone close (all pre-existing from prior milestones, out of v0.23.0 scope):

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| verification_gap | Phase 01 (01-VERIFICATION.md) | human_needed | 2026-06-08 |
| verification_gap | Phase 03 (03-VERIFICATION.md) | gaps_found | 2026-06-08 |
| quick_task | pg-stress (20260521) | missing | 2026-06-08 |
| quick_task | worker-retry (20260521) | missing | 2026-06-08 |
| quick_task | worker-security (20260521) | missing | 2026-06-08 |
| quick_task | 260525-myr-make-compat-rq-py-backend-agnostic-so-st | unknown | 2026-06-08 |

## Session Continuity

Last session: 2026-06-10
Stopped at: Roadmap created for v0.24.0 (Phases 12–18); requirements traceability filled; phase context files written from ingest
Resume file: None

## Operator Next Steps

- Plan the first phase with /gsd-plan-phase 12
