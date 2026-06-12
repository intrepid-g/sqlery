# Phase 12: quick-wins - Context

**Gathered:** 2026-06-10 (doc ingest — decisions pre-locked; no discussion phase needed)
**Status:** Ready for planning

<domain>
## Phase Boundary

Independently shippable quick wins (ingest Phase 1; **PLAN.md Steps 1–2**) plus the user-approved Python-floor raise. Always ships first (D10); no dependency on later phases.

- **Partial pending index (R1, Step 1):** replace the full composite index at `src/sqlery/django_sqlery/models.py:592` with `(queue_name, priority DESC, created_at) WHERE status='queued'`, name `sqlery_job_pending_idx`. Migration `0028_partial_pending_index.py`, `atomic = False`, `AddIndexConcurrently`/`RemoveIndexConcurrently`. Definition must be byte-identical to Phase 15's migration-0029 DDL.
- **Batched DELETE cleanup (R2, Step 2):** keyset-batched loop in BOTH backends (`src/sqlery/django_sqlery/backend.py:455`, `src/sqlery/fastapi_sqlery/backend.py:674`): BATCH=500, `order_by("id")` subselect, finished-status re-check inside the DELETE, 0.1 s inter-batch sleep. This is the permanent SQLite / non-partitioned-PG path, not a stopgap.
- **Python 3.13 floor (R11):** `requires-python = ">=3.13"` in pyproject.toml; CI matrix drops 3.11/3.12; PROJECT.md constraint already updated.

**Mapped requirements:** R1 (REQ-partial-pending-index), R2 (REQ-batched-delete-cleanup), R11 (REQ-python-313-floor). R10 partially — SQLite path must remain untouched.

</domain>

<decisions>
## Implementation Decisions (LOCKED — do not re-ask, do not re-litigate)

- **D6 — SQLite keeps the (batched) DELETE path forever.** No partitioning emulation for SQLite; this phase's batched loop is the permanent fallback path for SQLite and non-partitioned PG.
- **D7 — Verified literals.** Status literal is `'queued'` (models.py:351); claim ordering is `-priority, created_at` (backend.py:870-874). The pending-index trailing column is `created_at` — NOT `scheduled_at` — byte-identical between this phase's 0028 index and Phase 15's 0029 DDL. If either definition changes, change both.
- **D10 — Phase ordering is fixed.** This phase always runs first and is independently shippable to sqlery-public immediately.
- **Python floor (user resolution, 2026-06-10, INGEST-CONFLICTS.md Resolution Log):** RAISE the floor to `">=3.13"`. The packaging change is in-scope as an explicit early task: pyproject bump, CI matrix drops 3.11/3.12, PROJECT.md constraint updated. 3.13+ syntax is then permitted throughout new code.

### Claude's Discretion
- Test structure/naming for the lock-hold-duration and mid-loop-claim-race tests
- Exact expression of the updated CI matrix entries

</decisions>

## Success Criteria (verbatim from GSD-CONTEXT.md Phase 1, plus R11)

1. New index used by the claim query (EXPLAIN shows it); old index gone.
2. Cleanup of a 100k-row backlog never holds a lock > 1 s and never deletes a row claimed mid-loop (test exists).
3. SQLite path untouched.
4. `requires-python = ">=3.13"` in pyproject.toml; CI matrix updated accordingly.

## Verification Anchors (from intel/constraints.md)

- SQLite × PG divergence: SQLite path untouched is this phase's half of R10.
- Index DDL byte-identity with migration 0029 (re-checked at Phase 15) — `(queue_name, priority DESC, created_at) WHERE status = 'queued'`, name `sqlery_job_pending_idx`.
- Rollback: Steps 1–2 are plain reverts (PLAN.md Step 13).

<canonical_refs>
## Canonical References

### Technical spec
- `.planning/intel/ingest-src/PLAN.md` — Steps 1–2 (corrected code blocks; concurrent index ops rationale in senior findings #3, #4, #5, #12, #14)
- `.planning/intel/requirements.md` — R1, R2 (+ R10, R11 context)
- `.planning/REQUIREMENTS.md` — R11 acceptance
- `.planning/intel/constraints.md` — schema/SQL contracts (index DDL), execution conventions
- `.planning/INGEST-CONFLICTS.md` — Resolution Log (Python-floor decision)

### External reference artifacts (read-only, outside this repo)
- `/Users/gabriel/Documents/GitHub/empty/sqlery-pgque/PLAN.md` — authoritative 13-step spec (original)
- `/Users/gabriel/Documents/GitHub/empty/sqlery-pgque/sqlery-vs-pgque.md` — rationale only; its Appendix A/B literals are SUPERSEDED (use created_at trailing column, not scheduled_at)

</canonical_refs>

<code_context>
## Existing Code Insights

- Existing full composite index: `models.Index(fields=["queue_name", "status", "-priority", "created_at"])` at `src/sqlery/django_sqlery/models.py:592`
- Claim ordering: `order_by("-priority", "created_at")` at `src/sqlery/django_sqlery/backend.py:870-874`
- Latest Django migration is `0027_*`; this phase adds `0028_partial_pending_index.py`
- Unbounded deletes to replace: `django_sqlery/backend.py:455` (`query.delete()`) and `fastapi_sqlery/backend.py:674`

## Execution Conventions (intel/constraints.md)

- Conventional single-line commits `(type): description`, < 50 chars, never mention AI
- When changing existing lines: comment out the wrong line, add the corrected line beneath — never delete/replace outright
- Track regressions in `REGRESSIONS.md`; pure functions preferred; complexity ≤ 10; tests describe behavior

</code_context>

<deferred>
## Deferred Ideas

None — scope fixed by ingest. Partition machinery starts in Phase 13.

</deferred>

---

*Phase: 12-quick-wins*
*Context gathered: 2026-06-10 (doc ingest)*
