# Phase 16: backend-wiring-pruning - Context

**Gathered:** 2026-06-10 (doc ingest — decisions pre-locked; no discussion phase needed)
**Status:** Ready for planning

<domain>
## Phase Boundary

Django backend wiring + claim-path pruning (ingest Phase 5; **PLAN.md Steps 9–10**). Depends on Phase 15 (table is now partitioned).

- **Backend wiring (Step 9):** `_partitioned_pg()` helper (`connection.vendor == "postgresql"`, default true for PG); `cleanup_jobs` (backend.py:455) routes to `reclaim_drained_partitions` when partitioned; `vacuum_database` (backend.py:529) skips VACUUM on `sqlery_queued_job` when partitioned (DROP leaves nothing to vacuum; keep VACUUM for other tables). Mirror ALL changes in `async_backend.py`.
- **Write-path pruning (Step 10):** add `created_at` to every id-only write path so PG prunes to one partition — the complete 11-item checklist:
  1. `db_compat.py:100` `atomic_claim_job_sqlite` — `filter(id=..., version=...)` CAS
  2. `db_compat.py:139` `atomic_claim_job_postgres` — `filter(id=..., version=...)` CAS
  3. `models.py:623` `mark_running`
  4. `models.py:656` `mark_success`
  5. `models.py:707` `mark_failed`
  6. `models.py:823` `save_meta` (also rewritten in Phase 15's audit)
  7. `backend.py:294` cancel queued job
  8. `backend.py:632` archive failed
  9. `backend.py:637-642` retry-chain status walk
  10. `backend.py:772` fetch by id
  11. `backend.py:898` job field update (plus `backend.py:792` `child_pid` update)

  `get_claimable_jobs` (backend.py:846-877) fetches full model rows, so `job.created_at` is already in hand — no extra query; thread it into every subsequent UPDATE. Verify no `.only()`/`values()` on the claim path trims `created_at`.
- **Completes R9 and R10:** all five operator metrics exist by end of this phase; SQLite × PG divergence matrix runs every backend method under both vendors.

**Mapped requirements:** R3 (REQ-partition-drop-reclaim — wiring half), R6 (REQ-single-partition-writes — write-path half); R9 metrics complete; R10 divergence matrix green.

</domain>

<decisions>
## Implementation Decisions (LOCKED — do not re-ask, do not re-litigate)

- **D5 — Destroy-by-default with archive hook:** the reclaim path that `cleanup_jobs` now routes to destroys failed-job history beyond retention unless `SQLERY_PARTITION_ARCHIVE_HOOK` is configured. Document loudly at the routing point.
- **D6 — SQLite keeps the batched DELETE path:** `_partitioned_pg()` false (SQLite or non-partitioned PG) keeps the Phase-12 batched loop. SQLite behavior must be byte-for-byte unchanged.
- **D7 — Verified literals:** claim ordering `-priority, created_at`; the CAS filters gain `created_at`, never lose `version`. Optimistic locking semantics unchanged.
- **D10 — Phase ordering fixed.**

### Claude's Discretion
- How EXPLAIN-based pruning tests are structured (one per checklist item is the acceptance bar)
- Where the remaining metrics hook into the daemon stats path

</decisions>

## Success Criteria (verbatim from GSD-CONTEXT.md Phase 5)

1. EXPLAIN on each checklist write path shows single-partition pruning.
2. Full claim → run → complete → reclaim lifecycle test passes on a partitioned table.
3. SQLite divergence matrix green.

## Verification Anchors (from intel/constraints.md)

- The 11-item write-path checklist (PLAN.md Step 10) is a LITERAL acceptance checklist — every item gets an EXPLAIN-verified pruning test.
- Test matrix items owned here: claim → complete round-trip lands on a partitioned table (CAS with `created_at`); SQLite × PG divergence matrix over every backend method.
- All five metrics must exist by end of this phase: partition count, DEFAULT-partition row count (alert > 0), oldest undrained partition age, staging-table depth, maintenance-tick duration.

<canonical_refs>
## Canonical References

### Technical spec
- `.planning/intel/ingest-src/PLAN.md` — Steps 9–10 (senior finding #13 = the checklist inventory), Step 13 (metrics list)
- `.planning/intel/requirements.md` — R3, R6, R9, R10
- `.planning/intel/constraints.md` — verification anchors (checklist, metrics, matrix)

### External reference artifacts (read-only, outside this repo)
- `/Users/gabriel/Documents/GitHub/empty/sqlery-pgque/PLAN.md` — original spec
- `/Users/gabriel/Documents/GitHub/empty/sqlery-pgque/sql/pgwq.sql` — lifecycle reference behavior

</canonical_refs>

<code_context>
## Existing Code Insights

- `src/sqlery/django_sqlery/db_compat.py` — both CAS claim functions; `FOR UPDATE SKIP LOCKED` at db_compat.py:58
- `src/sqlery/django_sqlery/backend.py` + `async_backend.py` — cleanup, vacuum, and the id-only write paths listed above
- Version-based optimistic locking filters on `id` + `version` (never `pk`) — adding `created_at` must not change CAS semantics; verify the SQLite path in tests
- Daemon stats path (Phase 13 added the DEFAULT-partition metric) hosts the remaining four metrics

## Execution Conventions (intel/constraints.md)

- Conventional single-line commits `(type): description`, < 50 chars, never mention AI
- When changing existing lines: comment out the wrong line, add the corrected line beneath — never delete/replace outright
- Track regressions in `REGRESSIONS.md`; pure functions preferred; complexity ≤ 10; tests describe behavior

</code_context>

<deferred>
## Deferred Ideas

- The same wiring + pruning for `fastapi_sqlery/` — Phase 17

</deferred>

---

*Phase: 16-backend-wiring-pruning*
*Context gathered: 2026-06-10 (doc ingest)*
