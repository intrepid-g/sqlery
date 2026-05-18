# Sqlery Backlog

Items captured outside the current milestone. Promote to a new milestone via `/gsd-new-milestone` when ready.

## Current milestone routing

- **Promoted to active milestone:** `v0.22 — Stability, Coverage, and Operational Confidence`
- **Why:** trust, battle-testing, and CI signal have higher leverage right now than adding another permanent compat surface

## Next-milestone candidates

### Drop-in compatibility — permanent first-class feature (2026-05-15)

**Direction:** Users migrating from Celery, RQ, or django-tasks-scheduler should change ONLY their import paths. Compat shims stay forever; they are NOT a deprecation runway.

This contradicts `src/sqlery/compat/rq.py` which currently says "Deprecated since v3.1.0 — will be removed in v3.2.0". That deprecation must be reversed in the next milestone.

**Work items:**

1. **New: `sqlery.compat.celery`** — currently missing. Needs to provide:
   - `@app.task(...)` / `@shared_task(...)` decorator with the same kwargs (bind, name, queue, retry, autoretry_for, max_retries, default_retry_delay, soft_time_limit, time_limit, acks_late, ignore_result).
   - `.delay(*args, **kwargs)` and `.apply_async(args, kwargs, eta, countdown, expires, queue, link, link_error)` methods on decorated callables.
   - `AsyncResult` / `EagerResult` API surface (`.get()`, `.ready()`, `.successful()`, `.failed()`, `.id`, `.status`).
   - `current_app`, `Celery(name)` stub class — enough that `Celery("myproj")` doesn't crash but routes to sqlery.
   - `signature`/`subtask` / `chain` / `group` / `chord` — Phase 1 of celery compat can skip canvas; document gap.
   - Decision: which sqlery queue does a `@shared_task` map to by default? Most apps have a single default queue; the shim can use `"default"` until explicit.
2. **Audit `sqlery.compat.rq`** — remove the v3.2.0 deprecation notice in `compat/rq.py:13` and the `warnings.warn(...)` call in `compat/rq.py:27`. Replace with a "stable drop-in" header. Add any RQ APIs that are missing (especially `Queue.enqueue_in`, `@job(queue, timeout=...)`, `Retry`, `get_current_job`, `Connection`).
3. **Audit `sqlery.compat.scheduler`** — verify `@job(queue_name)` decorator is exported and behaves like django-tasks-scheduler's. Currently grep shows only dataclass decorators in the file; the `@job` decorator may be missing or named differently.
4. **Contract tests:** for each compat module, write a test that imports a representative slice of the original library's public API from `sqlery.compat.X` and exercises the basic enqueue/decorate/result flow. This is the regression net that keeps drop-in actually drop-in.
5. **Docs:** add a "Migrating from Celery / RQ / django-tasks-scheduler" guide. One mapping table per source library showing `old import → new import`.

**Why this matters:** the project's strategic moat is "swap one import line and get a Postgres-backed task queue with no Redis/RabbitMQ". Letting compat shims rot or deprecating them undermines that.

**Estimated size:** 1 phase (~4-6 plans) for the celery shim + a 1-2 plan audit for rq/scheduler. Total ~6-8 plans, likely the milestone after the maturity pass.

**Prepared context:** `.planning/research/COMPAT-SUMMARY.md`

---

## Lower-priority / future

- LocalStack / SAM Lambda fidelity testing (deferred from Phase 2 — currently smoke-only).
- Coverage gate path from 13% → 70% — requires fixing the 196 Django test-fixture collection errors that suppressed Phase 3's baseline measurement.
- Audit logging on dashboard actions (who did what, when) — out of scope from Phase 4 SEC pass.
- Rate limiting on the dashboard.
- Encrypting persisted job payloads at rest.
- Quarterly dead-code retention sweep — each `#CLEANUP` marker has a "Remove after YYYY-MM-DD"; that date arrives, a separate decision is made about deletion.
- Phase 1 standalone-no-django CI human-verify — push branch, confirm the job goes green. Independent ops task.
