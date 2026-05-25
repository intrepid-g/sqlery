---
phase: 260525-myr
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - src/sqlery/compat/rq.py
  - tests/test_compat_rq_standalone.py
autonomous: true
requirements:
  - make-compat-rq-standalone
must_haves:
  truths:
    - "import sqlery.compat.rq succeeds in a process where Django is not installed or not configured"
    - "All existing Django-mode behavior is unchanged — Queue, get_queue, get_job_registry_summary, clear_failed_jobs, delete_other_jobs_by_same_meta_tag, is_final_retry, get_queue_wait_time, requeue_if_jobs_pending, Job.fetch, Worker.all still work under Django"
    - "Standalone mode routes all DB operations through get_backend() and the mode-agnostic Queue in core/job_queue.py"
    - "__all__ in rq.py is unchanged"
  artifacts:
    - path: "src/sqlery/compat/rq.py"
      provides: "Backend-agnostic RQ compat layer"
      contains: "get_backend"
    - path: "tests/test_compat_rq_standalone.py"
      provides: "Import smoke test + standalone stub tests"
      contains: "test_import_without_django"
  key_links:
    - from: "src/sqlery/compat/rq.py"
      to: "src/sqlery/compat/__init__.py"
      via: "get_backend(), is_django_mode()"
      pattern: "get_backend\\(\\)"
    - from: "src/sqlery/compat/rq.py"
      to: "src/sqlery/core/job_queue.py"
      via: "Queue (standalone path)"
      pattern: "job_queue"
---

<objective>
Make src/sqlery/compat/rq.py importable and functional in standalone (non-Django) mode.

Purpose: RQ migrants running FastAPI/SQLAlchemy have no migration path because the module
hard-imports Django models at the top level, failing immediately when Django is absent.
This change routes all DB operations through get_backend() and the mode-agnostic abstractions
while keeping the Django fast-path intact.

Output: A revised rq.py with lazy/guarded Django imports, a new _make_queue() factory that
returns the right queue type per mode, and utility functions re-expressed via DatabaseBackend
ABC methods. A new test file proves the module imports without Django configured.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@src/sqlery/compat/rq.py
@src/sqlery/compat/__init__.py
@src/sqlery/core/job_queue.py
</context>

<interfaces>
<!-- Key abstractions the executor needs. -->

From src/sqlery/compat/__init__.py:
```python
def get_backend() -> DatabaseBackend: ...
def is_django_mode() -> bool: ...
def is_standalone_mode() -> bool: ...

# Relevant DatabaseBackend ABC methods:
def get_job_by_id(self, job_id: int): ...          # replaces QueuedJob.objects.get(pk=...)
def get_jobs(self, status, queue_name, limit, offset) -> list: ...   # replaces QueuedJob.objects.filter(...)
def count_jobs(self, status, queue_name) -> int: ...  # replaces .filter().count()
def cleanup_jobs(self, status, queue_name, ...) -> dict: ...  # replaces .filter().delete()
def cancel_job(self, job_id: int) -> bool: ...      # replaces job.status = 'cancelled'; job.save()
def get_worker_heartbeats(self, active_only: bool = True): ...  # replaces Worker.objects.filter(...)
def create_job(self, task_path, kwargs, queue_name, priority, scheduled_at, max_retries,
               retry_backoff, allow_parallel, timeout_seconds, ...) ...  # for requeue_if_jobs_pending
```

From src/sqlery/core/job_queue.py:
```python
class Queue:
    def __init__(self, name, priority=None, max_retries=None, retry_backoff=None,
                 allow_parallel=None, timeout_seconds=None): ...
    def enqueue(self, task_path: str, **kwargs): ...       # task_path is a dotted string
    def enqueue_at(self, run_at: datetime, task_path: str, **kwargs): ...
    # NOTE: no enqueue_in() — must compute scheduled_at = now + timedelta, call enqueue_at()
```

From src/sqlery/django_sqlery/queue.py:
```python
class Queue:
    def __init__(self, name='default', backend=None, default_timeout=None): ...
    def enqueue(self, func: Callable, *args, **kwargs): ...  # accepts callables + job_name, meta, etc.
    def enqueue_in(self, delay: timedelta, func, **kwargs): ...
    def enqueue_at(self, when: datetime, func, **kwargs): ...
```
</interfaces>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Rewrite module-level Django imports as lazy/guarded; add _make_queue() factory</name>
  <files>src/sqlery/compat/rq.py</files>
  <behavior>
    - Importing `sqlery.compat.rq` in a Python process where `django` is not in sys.modules
      (or Django settings are not configured) must NOT raise ImportError or
      django.core.exceptions.ImproperlyConfigured.
    - `Queue('default')` in standalone mode must not instantiate a DjangoQueue.
    - `get_queue('default')` in standalone mode must return a Queue that wraps the
      core job_queue.Queue (not the Django one).
    - All existing Django-mode Queue/get_queue/utility-function behavior is preserved unchanged.
  </behavior>
  <action>
Remove all four top-level Django-concrete imports:

```
from sqlery.django_sqlery.models import Worker as _Worker
from sqlery.django_sqlery.models import QueuedJob
from sqlery.django_sqlery.queue import Queue as _DjangoQueue
from sqlery.django_sqlery.backend import DjangoBackend as _DjangoBackend
```

Replace with lazy helpers inside `_get_django_models()` and `_get_django_queue_cls()` that
import inline only when called, guarded by `is_django_mode()`.

Keep the import of `from sqlery.compat.scheduler import Retry, get_current_job, JobStatus`
(scheduler.py is Django-only but that is a separate issue; guard it the same way if needed
— but since rq.py re-exports these, wrap the scheduler import in a try/except that falls back
to inline Retry/JobStatus definitions for standalone, so callers that only need the RQ shim
don't need scheduler either).

Actually: `Retry` and `JobStatus` are defined in scheduler.py but are pure Python dataclasses
with no Django dependency. Check whether their definitions in scheduler.py have Django imports
at module level — they do (`from django.db.models import Q`, `from django.utils import timezone`
at the top of scheduler.py). So the import of scheduler.py also hard-fails without Django.

Strategy for the scheduler re-export: copy the pure Retry and JobStatus definitions directly
into rq.py (they are small, self-contained) so rq.py has zero cross-module Django dependency
at import time. Keep re-exporting get_current_job lazily.

Replace `_make_django_queue(name)` with a `_make_queue(name)` factory:

```python
def _make_queue(name: str):
    from sqlery.compat import is_django_mode
    if is_django_mode():
        from sqlery.django_sqlery.queue import Queue as _DQ
        from sqlery.django_sqlery.backend import DjangoBackend as _DB
        return _DQ(name, backend=_DB())
    else:
        from sqlery.core.job_queue import Queue as _CoreQ
        return _CoreQ(name)
```

The rq.Queue class stores `self._q = _make_queue(name)` (same as today but deferred).

The rq.Queue.enqueue/enqueue_in/enqueue_at methods need a mode-branch because the core
Queue.enqueue() takes a dotted `task_path: str` but Django Queue.enqueue() takes a
`Callable`. In Django mode: call `self._q.enqueue(func, **mapped)` as today. In standalone
mode: resolve `task_path = f"{func.__module__}.{func.__qualname__}"`, then call
`self._q.enqueue(task_path, **mapped)`. For `enqueue_in` (no equivalent on core Queue),
compute `run_at = datetime.now(UTC) + delay` and call `self._q.enqueue_at(run_at, task_path,
**mapped)` in standalone mode.

The Queue.enqueue return type annotation changes from `QueuedJob` to the job instance (no
specific type annotation — just remove the concrete Django type or use `Any`).
</action>
  <verify>
    <automated>cd /Users/user/Documents/GitHub/sqlery && python -c "
import sys
# Simulate standalone: ensure django is not configured before importing rq
# (Django may be installed, but settings must not be configured)
import django.conf
# Reset to unconfigured state if already set up from environment
if django.conf.settings.configured:
    # Can't un-configure; just verify the import itself does no DB touch
    pass
from sqlery.compat import rq as _rq
print('import OK')
print('__all__:', _rq.__all__)
assert 'Queue' in _rq.__all__
assert 'Retry' in _rq.__all__
assert 'get_current_job' in _rq.__all__
print('PASS')
"
    </automated>
  </verify>
  <done>
    The four Django concrete top-level imports are gone from rq.py. `_make_queue()` factory
    exists. Retry and JobStatus are defined inline in rq.py (not imported from scheduler).
    get_current_job is imported lazily inside the function or via a thin wrapper.
  </done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Route utility functions through DatabaseBackend ABC; add standalone guards</name>
  <files>src/sqlery/compat/rq.py</files>
  <behavior>
    - get_job_registry_summary(queue_name) in standalone mode returns the same dict shape using
      backend.get_jobs(queue_name=queue_name) iteration.
    - clear_failed_jobs(queue_name) in standalone mode calls backend.cleanup_jobs(status='failed',
      queue_name=queue_name) and returns the deleted count from the result dict.
    - delete_other_jobs_by_same_meta_tag(current_job_id, meta_tag) in standalone mode iterates
      backend.get_jobs(status='queued', queue_name=None, limit=10000) and calls
      backend.cancel_job(job.id) for each match, same semantics as Django path.
    - get_queue_wait_time(queue_name) in standalone mode uses backend.get_jobs(status='queued',
      queue_name=queue_name, limit=1) sorted by created_at ascending (or falls back to 0).
    - requeue_if_jobs_pending(current_job, ...) in standalone mode uses backend.count_jobs(
      status='queued', queue_name=current_job.queue_name) for the pending check, then calls
      _make_queue(current_job.queue_name).enqueue_in() (already mode-aware from Task 1).
    - Worker.all() in standalone mode calls backend.get_worker_heartbeats(active_only=True).
    - Job.fetch(job_id) in standalone mode calls backend.get_job_by_id(job_id) and raises
      NoSuchJobError if None is returned.
    - is_final_retry(job) remains pure Python (no DB access) — no change needed.
    - Django fast path for every function is preserved identically.
  </behavior>
  <action>
For each of the six utility functions and two stub classes, add a mode-dispatch branch.
The pattern for every function is:

```python
def clear_failed_jobs(queue_name: str) -> int:
    if is_django_mode():
        # existing code unchanged
        from sqlery.django_sqlery.models import QueuedJob
        count, _ = QueuedJob.objects.filter(queue_name=queue_name, status="failed").delete()
        return count
    else:
        from sqlery.compat import get_backend
        result = get_backend().cleanup_jobs(status="failed", queue_name=queue_name)
        return result.get("deleted", 0)
```

Import `is_django_mode` from `sqlery.compat` at the top of the file (this import is safe —
compat/__init__.py has no Django imports at module level, only try/except).

For `get_job_registry_summary`, iterate the list returned by `backend.get_jobs(queue_name=
queue_name, limit=100000)` — each job object has `.status`, `.id`, and `.scheduled_at`
attributes in both Django and SQLAlchemy backends (verified from the ABC contract).

For `delete_other_jobs_by_same_meta_tag`, the standalone path iterates `backend.get_jobs(
status='queued', limit=10000)` (no queue filter since meta-tag match is global). Each job
object has a `.meta` attribute (dict or None) in both backends. Call `backend.cancel_job(
job.id)` for matches where `job.id != current_job_id`.

For `get_queue_wait_time`, use `backend.get_jobs(status='queued', queue_name=queue_name,
limit=1, offset=0)` — but note that the ABC's get_jobs ordering may not be by created_at.
As a safe standalone fallback: return 0 if the list is empty, otherwise compute
`datetime.now(UTC) - jobs[0].created_at` but acknowledge ordering is backend-defined
(document this limitation in the docstring).

For `requeue_if_jobs_pending`, the standalone `current_job` will be a SQLAlchemy Job model,
not a Django QueuedJob. Access `.queue_name`, `.task_path`, `.max_retries`, `.retry_backoff`,
`.priority`, `.job_name`, `.meta`, `.kwargs` — these field names are the same on both models
per the project conventions. The `_make_queue(name).enqueue_in()` call in Django mode calls
`_DjangoQueue.enqueue_in(delay, func, ...)`. In standalone mode, since core Queue has no
`enqueue_in`, compute `run_at = datetime.now(UTC) + timedelta(seconds=delay_seconds)` and
call `_make_queue(name).enqueue_at(run_at, task_path, **enqueue_kwargs)`.

For `Worker.all()`, return `get_backend().get_worker_heartbeats(active_only=True)`. The
returned objects will be WorkerProcess/heartbeat rows, not the Django Worker ORM model. This
is an acceptable type change for standalone callers — document it.

For `Job.fetch()`, use `get_backend().get_job_by_id(int(job_id))`.
For `Job.delete()` in standalone mode, call `get_backend().cancel_job(self._qj.id)` (the ABC
has no `delete_job` method; cancel is the closest equivalent — document this).
  </action>
  <verify>
    <automated>cd /Users/user/Documents/GitHub/sqlery && python -m pytest tests/test_compat_rq_standalone.py -x -q 2>&1 | tail -20</automated>
  </verify>
  <done>
    All six utility functions and two stub classes have a standalone branch routing through
    get_backend(). No function directly references Django models or imports at call time in
    standalone mode. Tests in test_compat_rq_standalone.py pass.
  </done>
</task>

<task type="auto">
  <name>Task 3: Write test_compat_rq_standalone.py — import smoke test + standalone stub coverage</name>
  <files>tests/test_compat_rq_standalone.py</files>
  <action>
Create tests/test_compat_rq_standalone.py. The file must NOT use pytest-django fixtures or
Django test infrastructure — it runs in a context where Django settings are configured
(CI does configure Django), but the tests explicitly exercise the standalone code paths by
calling functions with a mock/stub backend.

Structure the file as follows:

1. A plain `test_import_succeeds` that does `import sqlery.compat.rq` and asserts that
   `__all__` contains the expected names. This proves the top-level import has no hard Django
   dependency beyond what is already in `sys.modules`.

2. A `MockBackend` class inheriting from `DatabaseBackend` ABC that implements all abstract
   methods as stubs returning empty/zero values. Concrete overrides for the methods called by
   rq.py utility functions:
   - `get_jobs(...)` → returns a configurable list of fake job objects
   - `count_jobs(...)` → returns a configurable int
   - `cleanup_jobs(...)` → returns `{"deleted": 2}`
   - `get_job_by_id(job_id)` → returns a fake job or None
   - `cancel_job(job_id)` → returns True
   - `get_worker_heartbeats(active_only=True)` → returns a list of fake worker dicts

3. A `FakeJob` dataclass with fields: `id`, `status`, `queue_name`, `scheduled_at`,
   `created_at`, `meta`, `task_path`, `kwargs`, `max_retries`, `retry_backoff`, `priority`,
   `job_name` — all with sensible defaults. This is the job object the mock returns.

4. Tests using `unittest.mock.patch("sqlery.compat.is_django_mode", return_value=False)` and
   `unittest.mock.patch("sqlery.compat.rq._get_backend", ...)` to force standalone mode and
   inject the MockBackend:

   - `test_get_job_registry_summary_standalone` — verifies the returned dict has the five
     expected keys and that jobs are bucketed correctly by status.
   - `test_clear_failed_jobs_standalone` — verifies the deleted count is returned.
   - `test_delete_other_jobs_by_same_meta_tag_standalone` — verifies only jobs with matching
     meta['tag'] (excluding current_job_id) are cancelled.
   - `test_get_queue_wait_time_empty_standalone` — returns 0 for empty queue.
   - `test_worker_all_standalone` — returns the list from get_worker_heartbeats.
   - `test_job_fetch_not_found_standalone` — raises NoSuchJobError.
   - `test_job_fetch_found_standalone` — returns a Job wrapping the FakeJob.

Note: The patch target for is_django_mode must be `sqlery.compat.rq.is_django_mode` (the
name as imported in rq.py's module namespace), not `sqlery.compat.is_django_mode`.
Similarly patch `sqlery.compat.rq.get_backend` (or whichever name rq.py calls it by).

Use `pytest.mark.django_db` only if database access is actually needed — these tests use
mocks so no DB access is required; do not add the marker.
  </action>
  <verify>
    <automated>cd /Users/user/Documents/GitHub/sqlery && python -m pytest tests/test_compat_rq_standalone.py -x -q 2>&1 | tail -20</automated>
  </verify>
  <done>
    tests/test_compat_rq_standalone.py exists. All tests pass. The import smoke test runs
    without configuring Django differently. The standalone branch tests cover all six utility
    functions plus Job.fetch and Worker.all.
  </done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| caller → rq.py utility functions | job_id and meta_tag inputs from application code |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-myr-01 | Tampering | delete_other_jobs_by_same_meta_tag | accept | meta_tag is compared in-process against DB values; no SQL injection risk since comparison is Python-side after ORM/backend fetch |
| T-myr-02 | Information Disclosure | Job.fetch (standalone) | accept | Returns job data to caller; already Django-mode risk; no new exposure |
</threat_model>

<verification>
1. `python -m pytest tests/test_compat_rq_standalone.py -x -q` — all tests pass
2. `python -m pytest tests/ -x -q --ignore=tests/test_compat_rq_standalone.py` — no regressions
3. `python -c "from sqlery.compat.rq import Queue, Retry, get_queue, Job, Worker, NoSuchJobError, JobStatus, get_current_job, get_job_registry_summary, clear_failed_jobs, delete_other_jobs_by_same_meta_tag, is_final_retry, get_queue_wait_time, requeue_if_jobs_pending; print('all imports OK')"` — prints "all imports OK"
4. `grep -n "^from sqlery.django_sqlery" src/sqlery/compat/rq.py` — zero results (no top-level Django imports remain)
</verification>

<success_criteria>
- `grep -c "^from sqlery.django_sqlery\|^from django" src/sqlery/compat/rq.py` returns 0 (all Django imports are inside functions)
- `python -m pytest tests/test_compat_rq_standalone.py` passes with 8+ test cases
- Existing test suite passes without regression (`python -m pytest tests/ -q`)
- `__all__` in rq.py is byte-for-byte identical to the original
</success_criteria>

<output>
After completion, create `.planning/quick/260525-myr-make-compat-rq-py-backend-agnostic-so-st/260525-myr-01-SUMMARY.md`
</output>
