# Milestone: Drop-In Compatibility — Autonomous-Compatible

> Status: Scoped via discovery interview, ready to convert with `/gsd-new-milestone`.
> Target: Permanent first-class compat shims for RQ, django-tasks-scheduler, and Celery.

## Summary

Sqlery promises that users coming from **RQ, django-tasks-scheduler, or Celery** change only
their import paths and keep their code — permanently, not as a migration aid.

Inventory of `src/sqlery/compat/`:
- ✅ The **core engine** (`core/scheduler.py`) is already backend-agnostic — no rebuild needed.
- ✅ **`rq.py`** is already Django-free *at import time* (lazy imports); **no "deprecated/will-be-removed" notes actually exist** in either shim.
- ❌ **`compat/scheduler.py`** is hard-coupled to Django (top-level `Q`, `django.utils.timezone`, direct `sqlery.django_sqlery.*` imports at lines 27–43). **By decision it stays Django-only** — its audience is Django users by definition.
- ❌ **`celery.py` does not exist.**

The earlier milestone had five gaps that would have stalled or mis-guessed under
`/gsd-autonomous`. All five are now closed by locked decisions, so the entire milestone can
run unattended.

### Locked decisions (close the autonomy gaps)
- **`AsyncResult.get(timeout)`** → **poll-with-timeout** (poll job row ~0.5s until done/timeout). Non-blocking-only alternative stays one-line-flippable.
- **chord/group** → **at-least-once callback, documented** (no heavy locking). Exactly-once and "chord scoped out" left as labeled alternatives.
- **Celery target** → **pin 5.4.x, add `celery` as a test-only dependency** so parity tests introspect *real* signatures and compare. This neutralizes the "no research phase" hallucination risk — a wrong signature fails a test.
- **Postgres** → **available locally**; all four matrix legs run, with a preflight that skips gracefully if PG is ever absent.
- **Signals** → **wire `task_prerun`/`task_postrun`/`task_failure` for real (asserted); all other signals importable no-ops.**
- **Worker CLI** → **API only**; users run sqlery's own worker. No `celery worker`/`beat` emulation.
- **Research** → **folded into planning** (safe because real-celery comparison tests backstop accuracy).

## Objectives

1. `compat/celery.py` built — broad parity (core + canvas + beat + result + signals), standalone-capable.
2. `rq.py` **and** `celery.py` pass `{Django, standalone} × {SQLite, Postgres}`.
3. `compat/scheduler.py` stays Django-only, guarded with a clear `ImportError`.
4. Compat documented as a permanent first-class feature.
5. Parity proven against **installed Celery 5.4** signatures.

## Can advance now (unattended) — all phases

### P1 — Guard + rq.py agnostic
- scheduler.py import guard: per repo convention, comment the bare `django`/`django_sqlery.*` imports (lines 27–43), add guarded versions raising `ImportError("sqlery.compat.scheduler requires Django; use sqlery.compat.rq or .celery for standalone")`.
- Audit every rq.py public API in standalone mode; fix Django-assuming runtime paths.
- Matrix tests for rq.py.

### P2 — Celery core
- `Celery()` app, `@app.task`/`@shared_task`, `Task`, `.delay()`, `.apply_async()` (countdown/eta/retry), `AsyncResult.status` + **poll-with-timeout `.get()`**.

### P3 — Celery canvas + beat + signals
- `chain`→job dependencies, `group`→batch enqueue, `chord`→dep+callback (**at-least-once**).
- `beat`→ScheduledTask/cron engine.
- `task_prerun`/`task_postrun`/`task_failure` fired + asserted; other signals importable no-ops.

### P4 — Parity tests + docs
- Full `{Django, standalone} × {SQLite, Postgres}` matrix per shim.
- `celery` 5.4 as test-only dep; assert shim signatures match installed Celery.
- Docs/changelog audit → rewrite any "temporary/migration" language to permanent; add policy note to each shim's module docstring.

### Advanced with a default — flip if wrong (one-line swaps)
- **`celery.py`** `RESULT_GET_MODE = "poll"` — alt `"nonblocking"` commented.
- **`celery.py`** `CHORD_FIRE = "at_least_once"` — alts `"exactly_once"`, `"disabled"` commented.
- **Postgres preflight** `PG_LEG = "auto"` (run if reachable) — alts `"required"` / `"ci_only"` commented.

## Needs your decision

**None.** Every prior blocker is now a locked default with a one-line alternative. The milestone
is fully autonomous-eligible.

## Proposed approach

Run `/gsd-autonomous` over all four phases in order (P1→P4). P1 is independent; P2–P4 build on
each other. Each phase's verification is concrete (named tests with assertions, signature
comparison vs real Celery, preflight-guarded PG legs), so the autonomous verifier won't hit
fuzzy pass/fail judgments.

## Open questions / assumptions

- **Assumption:** "broad parity" = commonly-imported public surface of Celery 5.4, not 100% of its API; edge APIs filled on demand.
- **Assumption:** adding `celery` as a **test-only** dep doesn't violate the "no new runtime dependencies" constraint — never imported by shipping code, only by the test suite. (Alternative: snapshot signatures to avoid even a dev dep.)
- **Assumption:** poll interval ~0.5s and a sane default timeout are acceptable; tunable via constant.

## Inventory reference (current compat surface)

- `compat/rq.py` — 20 exports, real implementations; Django-free at import time (lazy imports). Stub: `Worker` (only `.all()`).
- `compat/scheduler.py` — 24 exports, mostly real; Django-coupled at top level. Stubs: `TaskArg`, `TaskKwarg` (import-compat dataclasses).
- `compat/celery.py` — does not exist.
- Existing tests: `tests/test_compat_rq_standalone.py`, `tests/test_scheduler_compat.py`, `tests/test_parity_scheduler.py`.
