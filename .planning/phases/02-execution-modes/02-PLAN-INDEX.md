# Phase 02 — Plan Index

**Phase:** 02-execution-modes
**Created:** 2026-05-13
**Total plans:** 8 across 5 waves
**Total requirements covered:** 17 (DMOD-01..06, SMOD-01..06, ASYN-01..05)

## Wave Map

| Wave | Plan | Requirements | Autonomous | Depends on |
|------|------|--------------|------------|------------|
| 1 | [02-01](02-01-PLAN.md) Django 5.2 bump + CI matrix | (enables ASYN-02) | no (CI human-verify) | — |
| 1 | [02-02](02-02-PLAN.md) `shutting_down` status migrations | ASYN-05 (schema) | no (migration human-verify) | — |
| 1 | [02-03](02-03-PLAN.md) AsyncDatabaseBackend ABC | ASYN-01 | yes | — |
| 2 | [02-04](02-04-PLAN.md) DjangoAsyncBackend (native async ORM) | ASYN-02 | yes | 02-03 |
| 2 | [02-05](02-05-PLAN.md) SQLAlchemyAsyncBackend (aiosqlite + psycopg3-async) | ASYN-03 | yes | 02-03 |
| 3 | [02-06](02-06-PLAN.md) AsyncWorker rewrite + drain-with-deadline | ASYN-04, ASYN-05 | yes | 02-02, 02-04, 02-05 |
| 4 | [02-07](02-07-PLAN.md) Parametrized E2E harness + existing-mode tests | DMOD-01/02/03/05, SMOD-01/05 | yes | 02-02 |
| 5 | [02-08](02-08-PLAN.md) Net-new modes (subprocess-standalone, HTTP-trigger, Lambda, async E2E) | SMOD-02/03, DMOD-04, SMOD-04, DMOD-06, SMOD-06 | yes | 02-06, 02-07 |

## Dependency Graph

```
W1: 02-01  02-02  02-03
              |     |
              |     +-----------+
              |                 |
W2:           |       02-04   02-05
              |          \     /
              |           \   /
W3:           +----------> 02-06
              |
W4:           +----------> 02-07
                              |
W5:            02-06, 02-07 -> 02-08
```

Parallelism: W1 has 3 parallel plans; W2 has 2 parallel; W3/W4 single; W5 single (tasks within 02-08 may run in parallel branches).

## Requirement → Plan Coverage

| Requirement | Plan |
|-------------|------|
| ASYN-01 | 02-03 |
| ASYN-02 | 02-04 (+ enabled by 02-01) |
| ASYN-03 | 02-05 |
| ASYN-04 | 02-06 |
| ASYN-05 | 02-06 (+ schema in 02-02) |
| DMOD-01 | 02-07 |
| DMOD-02 | 02-07 |
| DMOD-03 | 02-07 |
| DMOD-04 | 02-08 |
| DMOD-05 | 02-07 |
| DMOD-06 | 02-08 |
| SMOD-01 | 02-07 |
| SMOD-02 | 02-08 |
| SMOD-03 | 02-08 |
| SMOD-04 | 02-08 |
| SMOD-05 | 02-07 |
| SMOD-06 | 02-08 |

Every Phase-2 requirement is covered by exactly one primary plan; ASYN-02 and ASYN-05 have supporting plans (02-01 enables; 02-02 schema-prep).

## Surfaced Assumptions / Decisions

- `[ASSUMED]` E2E harness shape = single parametrized `(mode, integration, db)` pytest matrix (CONTEXT decision B). Override-point: Plan 02-07.
- `[ASSUMED]` `SHUTDOWN_DEADLINE_SECONDS` default = 60s (CONTEXT decision C). Env-var override at runtime; no plan change required.
- `[ASSUMED]` CI matrix drops Django 4.2 hard, keeps 5.2 only (with optional 5.1 smoke). RESEARCH open-question #1.
- `[ASSUMED]` SQLite WAL pragma gap fix folded into Plan 02-05 (standalone async engine). RESEARCH open-question #2 — cheap, included.
- `[ASSUMED]` Top-level `sqlery/triggers.py` (147-line strategy file) LEFT LIVE; new `core/triggers.py` is a distinct HTTP-receiver surface. RESEARCH open-question #3.

## Notes

- The 21 legacy `sqlery.executor` callers (Phase-1 verification gap) are intentionally NOT in scope here per CONTEXT gotcha #1 — deferred to Phase 4.
- Phase-1 CI human-verify checkpoint (success criterion #1) is an independent ops task and does not block Phase-2 plan execution.
- AsyncWorker stub at top-level `src/sqlery/async_worker.py` follows the dated-stub policy (CLAUDE.md memory `feedback_dead_code.md`) — no outright deletion.
