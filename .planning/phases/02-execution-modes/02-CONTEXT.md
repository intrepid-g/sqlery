# Phase 02 — Execution Modes & Async Rebuild: Context

**Phase:** 02-execution-modes
**Created:** 2026-05-13
**Source of scope:** ROADMAP.md Phase 2 + REQUIREMENTS.md (DMOD-01..06, SMOD-01..06, ASYN-01..05)

## Canonical refs

- `.planning/ROADMAP.md` — Phase 2 definition (lines 38–49)
- `.planning/REQUIREMENTS.md` — DMOD-01..06 (lines 19–24), SMOD-01..06 (lines 28–33), ASYN-01..05 (lines 37–41)
- `.planning/PROJECT.md` — execution mode matrix, broken-state notes (AsyncWorker since v0.13)
- `.planning/phases/01-core-unification/01-CONTEXT.md` — Phase 1 deferred items routing AsyncWorker rebuild here
- `CLAUDE.md` — project constraints (Python 3.10+, fork safety, no new deps unless necessary)

## Decisions (locked — downstream agents must respect)

### A. Async Django backend strategy — Native async ORM

Use Django's **native async ORM methods** (`.aget()`, `.acreate()`, `.aupdate()`, async queryset iteration, async transactions). Do NOT wrap the sync ORM with `asgiref.sync_to_async`.

**Why:** Avoids thread-pool overhead per DB call; gives a clean async path through `DjangoBackend`; matches the standalone `SQLAlchemyBackend(async)` story.
**How to apply:** `ASYN-02` implementation lives entirely on native async APIs. Any helper method on `DatabaseBackend` that exists in async form (e.g., `aclaim_job`, `amark_running`) maps directly to a native async ORM call, not a sync-wrapper. Fork-safety rules from Phase 1 still apply (close connections around fork; only relevant to the daemon/subprocess modes, not the async worker).

### A.1. Django minimum version — bump to 5.2 LTS

Phase 02 raises the floor from `Django >= 4.2` to `Django >= 5.2`.

**Why:** 5.2 LTS (Apr 2025) has the async ORM features we need (async transactions, async manager methods on relations) without forcing the very latest 6.0. LTS keeps users on a stable line.
**How to apply:** Update `pyproject.toml` `django>=5.2`. Document the bump in the next release notes as a BC break. CI test matrix drops 4.2.

### C. AsyncWorker graceful shutdown — "drain-with-deadline"

On `SIGTERM`/`SIGINT`, the AsyncWorker performs a three-step shutdown:

1. **Stop polling immediately.** No new jobs are claimed.
2. **For each in-flight job:** set status to a new `shutting_down` (transient) state and start a configurable drain-deadline timer (default: `2 × job timeout`, bounded by `SHUTDOWN_DEADLINE_SECONDS`, default 60s).
3. **Race:** whichever wins, deterministically resolves the job —
   - If the job's `await execute()` resolves before the deadline → mark `finished` with its real result.
   - If the deadline elapses first → cancel the task, mark `failed` with error `"shutdown_timeout: worker terminated before job finished"`, and (when `max_retries > 0`) the standard retry path requeues it.

**Why:** "no in-progress jobs lost" needs an enforceable contract. A bare cancel loses work; an unbounded drain blocks redeploys. Drain-with-deadline gives the job a real chance to finish while keeping shutdown bounded.
**How to apply:**
- Add `shutting_down` to `QueuedJob.STATUS_CHOICES` (Django migration + Alembic migration).
- New env var `SQLERY_ASYNC_SHUTDOWN_DEADLINE_SECONDS` (default 60).
- ASYN-05 acceptance: SIGTERM during a job that finishes in <deadline ⇒ job marked `finished`; SIGTERM during a job that exceeds deadline ⇒ job marked `failed` with the deadline error and is re-eligible for retry if `max_retries > 0`.
- Race resolution uses `asyncio.wait({task, deadline}, return_when=FIRST_COMPLETED)`; `shutting_down` is a write-once state — once it flips to `finished`/`failed`, that wins.

### D. Standalone HTTP-trigger transport — Pure-core function adapted per integration

The trigger receiver logic lives in `src/sqlery/core/triggers.py` as a **pure function** that takes a parsed request envelope and returns a job-claim action. Integration layers adapt it:

- **Django:** `sqlery/django_sqlery/views.py` exposes a Django view that parses the request, calls `core.triggers.handle(envelope)`, returns a Django `JsonResponse`.
- **Standalone (FastAPI):** `sqlery/fastapi_sqlery/triggers.py` mounts a `POST /trigger` route on the existing `sqlery-web` FastAPI app; parses request → calls `core.triggers.handle(envelope)` → returns a FastAPI response.

**Why:** Shared semantics, framework-thin adapters. Avoids duplicating signature verification, idempotency, and dispatch logic.
**How to apply:**
- SMOD-03 ships both the core function and the FastAPI adapter.
- Existing Django HTTP-trigger middleware (per PROJECT.md "existing") is refactored to call the core function — not rewritten from scratch.
- The standalone trigger endpoint shares the `sqlery-web` ASGI app — no separate process.

### E. Lambda/serverless test strategy — deferred / smoke-only

DMOD-04 and SMOD-04 are satisfied by **import-and-call smoke tests** against the existing `lambda_handler.py`. No moto / LocalStack / SAM in this phase.

**Why:** Phase 02 already covers 17 requirements; full Lambda fidelity testing is high cost for marginal signal. The existing Lambda handler is "existing/works" per PROJECT.md.
**How to apply:**
- DMOD-04 acceptance: a pytest that imports `sqlery.lambda_handler`, constructs a fake EventBridge event dict, invokes the handler synchronously, asserts a job is enqueued/claimed.
- SMOD-04 acceptance: same shape, against the standalone handler path (which needs to be built — it currently has the Django-coupling that Phase 1 already addressed for `core/`).
- The full LocalStack/SAM end-to-end is deferred to a later phase (Phase 4 hardening or a dedicated test-fidelity follow-up).

### F. Phase shape — keep as one mega-phase

Do NOT split into 02a + 02b. Plan the work as a single phase with internal wave ordering so the async foundation (ASYN-01..03) lands before the modes that depend on it (DMOD-06, SMOD-06).

**Why:** User preference for cohesion. The internal dependencies are well-known; the planner can encode them as waves.
**How to apply:** When the planner runs, the wave DAG should look roughly:
- **Wave 1:** ASYN-01 (async ABC), Django-version bump (pyproject + CI matrix), `shutting_down` status migration
- **Wave 2:** ASYN-02 (Django async backend), ASYN-03 (SQLAlchemy async backend) — in parallel
- **Wave 3:** ASYN-04 (AsyncWorker rebuild) + ASYN-05 (shutdown semantics) — same plan
- **Wave 4:** DMOD-01..05 + SMOD-01, SMOD-05 — parametrized E2E tests for existing modes
- **Wave 5:** SMOD-02 (subprocess), SMOD-03 (HTTP trigger via D), DMOD-04/SMOD-04 (Lambda smoke per E), DMOD-06/SMOD-06 (async worker E2E)

## Decisions deferred to planner-default (no user input requested)

### B. E2E test harness shape

**Planner-default:** Single pytest-parametrized matrix indexed by `(mode, integration, db)`. Use pytest fixtures to wire each combination; mark slow/serverful combinations with `@pytest.mark.slow` so they can be skipped in inner-loop runs.

**Tag for planner:** `[ASSUMED]` — surface this in PLAN.md so the user can override during plan review.

## Specifics & gotchas to forward to researcher / planner

1. **Phase 1 left two pre-existing legacy callers** of top-level `sqlery.executor` stub in `src/sqlery/management/commands/run_jobs.py` and `run_scheduled_tasks.py` (per 01-VERIFICATION.md). Phase 02 should fold this cleanup into the mode work where touched, otherwise leave for Phase 4.
2. **AsyncWorker has been broken since v0.13** (PROJECT.md). Expect the existing `AsyncWorker` class to be a near-greenfield rewrite, not a refactor.
3. **`shutting_down` state** is a new value in `QueuedJob.STATUS_CHOICES` — Django migration + Alembic migration both required (matches existing two-source-of-truth migration pattern).
4. **Fork safety** is irrelevant in the async worker path (no `os.fork()`), but very relevant in daemon/subprocess modes for SMOD-01/02.
5. **Phase 1 success criterion #1** is still gated on the human-verify CI checkpoint (push branch, confirm `standalone-no-django` job goes green). Phase 02 plans should NOT block on this — it's an independent ops task.

## Deferred ideas (out of scope for Phase 02)

- **LocalStack / SAM Lambda fidelity testing** — Phase 4 or a dedicated follow-up.
- **Async-native scheduler** (the scheduler still polls synchronously in the daemon process). Functional, just not async-native. Not on the roadmap.
- **Per-mode harness split** — only if the parametrized matrix becomes painful to maintain.

## Open items for the researcher

1. Confirm Django 5.2 async-ORM coverage matches what `DatabaseBackend` needs (especially around `select_for_update` skip-locked semantics — does Django expose an async variant, or do we drop to raw SQL via `connection.acursor()` for the claiming path?).
2. Survey existing tests in `tests/` for the current daemon/subprocess E2E coverage in Django so the parametrized matrix doesn't duplicate.
3. Verify the existing standalone `lambda_handler` path (mentioned as "✗ Missing" in PROJECT.md table) — does it need a brand-new entry point, or does the existing handler just need its Django guards updated?

## Locked vs. negotiable

| Item | Status |
|---|---|
| Django 5.2 LTS minimum | **LOCKED** (BC break, documented in release notes) |
| Native async ORM (no sync_to_async) | **LOCKED** |
| Drain-with-deadline shutdown semantics + `shutting_down` state | **LOCKED** |
| Pure-core trigger function, framework adapters | **LOCKED** |
| Lambda smoke-only (no LocalStack) | **LOCKED** |
| Single-phase shape | **LOCKED** |
| Parametrized pytest matrix | **ASSUMED** — planner may revisit |
| `SHUTDOWN_DEADLINE_SECONDS` default = 60s | **ASSUMED** — researcher can recommend |
