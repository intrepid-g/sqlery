# Phase 18: listen-notify - Context

**Gathered:** 2026-06-10 (doc ingest — decisions pre-locked; no discussion phase needed)
**Status:** Ready for planning — OPTIONAL phase

<domain>
## Phase Boundary

Opt-in PG LISTEN/NOTIFY dispatch (ingest Phase 7; **PLAN.md Step 12**). Depends on Phase 17. **OPTIONAL: may be deferred or dropped without affecting milestone "done" (D10). No requirement maps here — this is the documented coverage exception.**

Cuts worker dispatch latency from up to 5 s (poll interval) to sub-100 ms. PG-only, gated behind `SQLERY_PG_NOTIFY = False` (opt-in — the ONLY flagged feature in this milestone).

When enabled:
- After enqueue, call `pg_notify('sqlery_job_<queue>', '')`.
- Worker opens a LISTEN connection and wakes on NOTIFY, falling back to polling on timeout.

**Mapped requirements:** none (optional latency improvement). Touches the enqueue path and worker poll loop in both adapters when enabled.

</domain>

<decisions>
## Implementation Decisions (LOCKED — do not re-ask, do not re-litigate)

- **D1 — `SQLERY_PG_NOTIFY = False` default** (opt-in).
- **D8 — Only LISTEN/NOTIFY is flagged:** partitioning itself has no flag; this is the single feature gate in the milestone.
- **D10 — This is the only droppable phase.** If milestone budget or risk says stop after Phase 17, the milestone is still "done".

### Claude's Discretion
- LISTEN connection lifecycle around `os.fork()` (must respect the existing fork-safety constraint: connections closed before fork)
- Channel-naming sanitization for queue names
- Async-worker integration shape

</decisions>

## Success Criteria (verbatim from GSD-CONTEXT.md Phase 7)

1. With flag on, dispatch latency < 100 ms in test.
2. With flag off (default), behavior is byte-identical to before.

## Verification Anchors (from intel/constraints.md)

- Flag-off path must be byte-identical — a divergence-matrix run with the flag off proves no regression.
- Fork safety: DB connection lifecycle around `os.fork()` is a standing project constraint; the LISTEN connection must not leak across forks.
- SQLite: no-op (LISTEN/NOTIFY is PG-only); SQLite behavior unchanged (R10 standing).

<canonical_refs>
## Canonical References

### Technical spec
- `.planning/intel/ingest-src/PLAN.md` — Step 12
- `.planning/intel/requirements.md` — "Phase 7 — LISTEN/NOTIFY" section (optional, no requirement)
- `.planning/intel/constraints.md` — config contract (`SQLERY_PG_NOTIFY`), existing project constraints (fork safety)

### External reference artifacts (read-only, outside this repo)
- `/Users/gabriel/Documents/GitHub/empty/sqlery-pgque/clients/python/pgwq/` — reference Python client; **`worker.py` is the LISTEN/NOTIFY loop reference implementation**
- `/Users/gabriel/Documents/GitHub/empty/sqlery-pgque/sql/pgwq.sql` — the `pg_notify` line in `enqueue` is the SQL-side reference

</canonical_refs>

<code_context>
## Existing Code Insights

- Worker poll loop: `src/sqlery/core/worker.py` (`WorkerProcess`) — pure 5 s polling today, no LISTEN/NOTIFY
- Enqueue path: `src/sqlery/core/job_queue.py` → backend `enqueue` in both adapters
- psycopg3 (`psycopg >= 3.1`) is async-capable and supports LISTEN/NOTIFY natively — no new dependency needed (project constraint)
- Signal-handler constraint: no DB calls in signal handlers; NOTIFY wake-up must integrate with the poll loop, not the SIGUSR1 handler

## Execution Conventions (intel/constraints.md)

- Conventional single-line commits `(type): description`, < 50 chars, never mention AI
- When changing existing lines: comment out the wrong line, add the corrected line beneath — never delete/replace outright
- Track regressions in `REGRESSIONS.md`; pure functions preferred; complexity ≤ 10; tests describe behavior

</code_context>

<deferred>
## Deferred Ideas

None — this IS the milestone's deferrable tail. If dropped, record the deferral in STATE.md and the milestone close notes.

</deferred>

---

*Phase: 18-listen-notify*
*Context gathered: 2026-06-10 (doc ingest)*
