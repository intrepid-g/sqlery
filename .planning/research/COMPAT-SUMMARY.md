# Research Summary: Future Compat Milestone

**Date:** 2026-05-15
**Status:** Deferred behind `v0.22` maturity work, but intentionally preserved for the next milestone.

## Why this exists

Compat remains strategically important. It is not the active milestone, but it is prepared work rather than a discarded idea. This note preserves the scoped research so the next milestone can start from concrete findings instead of redoing discovery.

## Existing Codebase Findings

### Already present

- `src/sqlery/compat/rq.py` exists and already wraps:
  - `Queue`
  - `get_queue()`
  - `Retry`
  - `get_current_job()`
  - `Job`, `Worker`, `NoSuchJobError`
  - `enqueue()`, `enqueue_in()`, `enqueue_at()`
- `src/sqlery/compat/scheduler.py` exists and already wraps:
  - `job`
  - `Queue`
  - `Task`, `TaskType`, `TaskArg`, `TaskKwarg`
  - `get_scheduled_task()`, `run_task()`
  - `JobModel`, `JobStatus`
- `tests/test_scheduler_compat.py` already gives a regression base for scheduler compatibility behavior.

### Current gaps / contradictions

- `compat.rq` still declares itself deprecated and emits `DeprecationWarning`.
- `compat.scheduler` also still emits `DeprecationWarning`.
- `sqlery.compat.celery` does not exist.
- Existing migration docs discuss Celery patterns, but there is no actual Celery compat module yet.
- Current scheduler compat warns that positional args are ignored in `create_and_enqueue_job()`, which is a behavioral gap for drop-in migration.

## Representative Upstream Surface

### Celery

Representative surface to target first:
- `@app.task(...)`
- `@shared_task(...)`
- `.delay(*args, **kwargs)`
- `.apply_async(args=None, kwargs=None, eta=None, countdown=None, queue=None, ...)`
- `AsyncResult` with `.id`, `.status`, `.ready()`, `.successful()`, `.failed()`, `.get()`
- `current_app`, `Celery(name)`

Likely deferred unless explicitly added:
- `signature`, `subtask`, `chain`, `group`, `chord`

### RQ

Representative public surface already close to useful:
- `Queue.enqueue()`
- `Queue.enqueue_in()`
- `Queue.enqueue_at()`
- `Retry(max=..., interval=...)`
- `get_current_job()`
- job/worker inspection helpers

Critical product gap:
- remove deprecation status and lock the supported surface with tests/docs

### django-tasks-scheduler

Representative public surface to preserve:
- `from scheduler import job`
- `@job()` / `@job("high")`
- queue wrappers
- scheduled task wrappers
- job/result inspection wrappers

Critical product gap:
- verify documented decorator/queue flows and remove deprecation signaling

## Recommended Compat Milestone Shape

1. **Celery shim**
   - add `sqlery.compat.celery`
   - task decorators
   - `.delay()` / `.apply_async()`
   - result wrapper
   - `Celery(name)` and `current_app`

2. **RQ stabilization**
   - remove deprecation warnings
   - define supported representative API
   - close parity gaps that block import-path migration

3. **Scheduler stabilization**
   - remove deprecation warnings
   - verify `@job` behavior
   - verify queue/task wrappers against tests and docs

4. **Contract tests**
   - one representative import/decorate/enqueue/result flow per compat module

5. **Migration docs**
   - explicit import mapping and supported gaps

## Risks

- overpromising full parity instead of a tested representative contract
- shipping Celery compat without a credible result wrapper
- leaving deprecation warnings in place while claiming permanent support
- hiding behavioral gaps around args, retries, callbacks, and scheduling options

## Planning note

Compat is intentionally deferred, not demoted. The current sequencing decision is:

- **Now:** strengthen trust in the existing six modes
- **Next:** promote compat using this preserved research and the backlog entry
