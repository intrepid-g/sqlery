# Phase 14: scheduled-job-staging - Context

**Gathered:** 2026-06-10 (doc ingest — decisions pre-locked; no discussion phase needed)
**Status:** Ready for planning

<domain>
## Phase Boundary

Far-future scheduled-job staging (ingest Phase 3; **PLAN.md Step 6**). Depends on Phase 13 — the daemon maintenance tick hosts the promotion loop. Problem: a job created today with `scheduled_at = T+60d` is a `queued` row pinning an otherwise-drained partition indefinitely.

- **`ScheduledJob` model (slim: queue, payload, scheduled_at, priority, max_retries)** in `django_sqlery/models.py` + migration `0030_scheduled_job_staging.py`. Plain table, no partitioning.
- **Enqueue routing:** jobs with `scheduled_at > now() + threshold` (default 1 day) INSERT into `sqlery_scheduled_job` instead of `sqlery_queued_job`.
- **Promotion (exactly-once):** `core/scheduler.py` `promote_due_scheduled_jobs(cur)` — one transaction: `DELETE FROM sqlery_scheduled_job WHERE scheduled_at <= now() + lookahead FOR UPDATE SKIP LOCKED RETURNING *` → `INSERT INTO sqlery_queued_job (...)`. Runs in the daemon loop on the Phase-13 tick.
- **Dual-table API surface:** status lookup, fetch-by-id, cancel/delete, and list must span BOTH tables in BOTH adapters — a job "disappearing" while staged is a bug. Enumerate and patch each consumer-facing read/write.
- **Shared id sequence:** job ids come from one shared sequence so an id never exists in both tables.
- **Config validation:** reject `SQLERY_PARTITION_RETENTION` ≤ staging threshold at config load (threshold ≪ retention is what makes the partition invariant hold).

**Mapped requirements:** R5 (REQ-scheduled-staging).

</domain>

<decisions>
## Implementation Decisions (LOCKED — do not re-ask, do not re-litigate)

- **D1 — Fixed defaults:** staging threshold = 1 day; `SQLERY_PARTITION_RETENTION = "30 days"` — an in-table job can pin its partition at most ~1 day past drain, comfortably inside retention.
- **D9 — Advisory locks:** `promote_due_scheduled_jobs` is wrapped in `pg_try_advisory_lock` like every maintenance function; `SKIP LOCKED` makes concurrent daemons safe and the advisory lock makes double-running cheap anyway.
- **D10 — Phase ordering fixed:** runs after Phase 13, before the Phase 15 cutover.

### Claude's Discretion
- Exact lookahead value relative to the daemon tick cadence
- Staging-table index choices
- How the dual-table API inventory is enumerated and tested

</decisions>

## Success Criteria (verbatim from GSD-CONTEXT.md Phase 3)

1. A job scheduled 60 days out is invisible to claims, visible to status/cancel APIs, promoted within one daemon tick of `scheduled_at - lookahead`.
2. Two daemons never double-promote (test with concurrent promoters).
3. Config validation rejects retention ≤ threshold.

## Verification Anchors (from intel/constraints.md)

- Test matrix item owned here: far-future `scheduled_at` job never pins a hot partition (staging path).
- Metric introduced here (complete by Phase 16): staging-table depth.
- Config validation invariant: `SQLERY_PARTITION_RETENTION` ≫ staging threshold; reject retention ≤ threshold at config load.

<canonical_refs>
## Canonical References

### Technical spec
- `.planning/intel/ingest-src/PLAN.md` — Step 6 (promotion semantics, dual-table surface, shared sequence; senior finding #6)
- `.planning/intel/requirements.md` — R5
- `.planning/intel/constraints.md` — config contract + validation invariants

### External reference artifacts (read-only, outside this repo)
- `/Users/gabriel/Documents/GitHub/empty/sqlery-pgque/PLAN.md` — original spec
- `/Users/gabriel/Documents/GitHub/empty/sqlery-pgque/sqlery-vs-pgque.md` — rationale only

</canonical_refs>

<code_context>
## Existing Code Insights

- `src/sqlery/core/scheduler.py` — existing Scheduler (v0.23.0 hardened cron path) gains `promote_due_scheduled_jobs`
- Enqueue path: `src/sqlery/core/job_queue.py` / `@job` decorator routes through the backend — the threshold split happens where `scheduled_at` is known
- Dual-table consumers to audit: status/fetch/cancel/list in `django_sqlery/backend.py`, `fastapi_sqlery/backend.py` (+ async variants), dashboard/admin views
- Migration `0030_scheduled_job_staging.py` follows Phase 15's `0029` in number but this phase lands before the cutover — numbering is fixed by the ingest (0028 index, 0029 cutover, 0030 staging)

## Execution Conventions (intel/constraints.md)

- Conventional single-line commits `(type): description`, < 50 chars, never mention AI
- When changing existing lines: comment out the wrong line, add the corrected line beneath — never delete/replace outright
- Track regressions in `REGRESSIONS.md`; pure functions preferred; complexity ≤ 10; tests describe behavior

</code_context>

<deferred>
## Deferred Ideas

- SQLAlchemy-side staging parity — Phase 17 (FastAPI parity re-verifies R5)

</deferred>

---

*Phase: 14-scheduled-job-staging*
*Context gathered: 2026-06-10 (doc ingest)*
