# RQ → sqlery Migration Guide (Agent-Optimized)

## Concept Mapping

| RQ | sqlery |
|----|--------|
| `Queue` | built-in queue (string name) |
| `job = queue.enqueue(fn, arg)` | `job = enqueue(fn, arg)` |
| `Worker` | daemon + worker pool (auto-managed) |
| `Scheduler` (rq-scheduler) | `ScheduledTask` (cron-based, built-in) |
| `StartedJobRegistry` | `JobRegistry` (`started`) |
| `FinishedJobRegistry` | `JobRegistry` (`finished`) |
| `FailedJobRegistry` | `JobRegistry` (`failed`) |
| `job.get_status()` | `job.status` |
| `job.result` | `job.output` |
| `job.exc_info` | `job.traceback` |
| Redis | PostgreSQL or SQLite |

---

## Common Patterns: Before → After

### 1. Enqueue a job

```python
# RQ
from redis import Redis
from rq import Queue

q = Queue(connection=Redis())
job = q.enqueue(my_function, arg1, kwarg1=val1)

# sqlery
from sqlery import enqueue

job = enqueue(my_function, arg1, kwarg1=val1)
```

### 2. Enqueue to a named queue with priority

```python
# RQ
job = Queue('high', connection=Redis()).enqueue(fn)

# sqlery
job = enqueue(fn, queue='high', priority=90)
```

### 3. Schedule a recurring task (cron)

```python
# RQ (rq-scheduler)
from rq_scheduler import Scheduler
scheduler = Scheduler(connection=Redis())
scheduler.cron('0 * * * *', func=my_function)

# sqlery (Django)
from sqlery.django_sqlery.models import ScheduledTask
ScheduledTask.objects.create(
    name='my_task',
    task_path='myapp.tasks.my_function',
    cron_expression='0 * * * *',
    queue_name='default',
    enabled=True,
)

# sqlery (standalone)
from sqlery import schedule_task
schedule_task('myapp.tasks.my_function', cron='0 * * * *', name='my_task')
```

### 4. Retry failed jobs

```python
# RQ
from rq.job import Job
failed_registry = FailedJobRegistry(queue=q)
for job_id in failed_registry.get_job_ids():
    Job.fetch(job_id, connection=Redis()).requeue()

# sqlery
from sqlery import get_backend
get_backend().retry_failed_jobs()  # retries all failed jobs
get_backend().retry_failed_jobs(queue_name='high')  # specific queue
```

### 5. Initialization / startup

```python
# RQ (standalone)
redis_conn = Redis()
q = Queue(connection=redis_conn)
worker = Worker([q], connection=redis_conn)
worker.work()

# sqlery (standalone)
from sqlery import initialize
initialize(
    database_url='postgresql://localhost/mydb',
    max_workers=4,
    worker_queues=['high', 'default', 'low'],
)
# Workers start automatically when daemon is running
# Run: sqlery daemon start
```

---

## Configuration Mapping

| RQ | sqlery (`initialize()` or Django settings) |
|----|---------------------------------------------|
| `Queue('name')` | `worker_queues=['name']` |
| `Worker(queues=[q1, q2])` | `worker_queues=['q1', 'q2']` |
| Redis URL | `database_url='postgresql://...'` |
| `job_timeout` | `timeout_seconds=` on `enqueue()` |
| `result_ttl` | `JOB_RETENTION` config |
| `failure_ttl` | `JOB_RETENTION.failed.max_age_days` |

---

## CLI Mapping

| RQ | sqlery |
|----|--------|
| `rq worker` | `sqlery daemon start` |
| `rq info` | `sqlery status` |
| `rq empty <queue>` | — (cancel via admin or API) |
| `rqscheduler` | built-in (daemon handles scheduling) |

---

## Unsupported / Differences

- **No Redis**: sqlery uses SQL — no pub/sub, no `job.ttl`, no `result_ttl` in the Redis sense
- **No `job.result`**: use `job.output` (string)
- **No `job.dependency`**: no `depends_on=` chaining
- **No `Callbacks`**: no `on_success`/`on_failure` callbacks (use retry + `max_retries`)
- **No burst mode**: daemon runs continuously
- **Queue priority** is numeric (0–100), not separate queues with workers

---

## Known Gotchas

These are specific RQ patterns that have no direct equivalent in sqlery and require code changes.

### 1. `get_current_job()` — No equivalent

RQ injects the running job into the worker's thread via a thread-local stack. sqlery has no equivalent context variable.

```python
# RQ — works inside a worker
from rq import get_current_job
job = get_current_job()
job.meta['progress'] = 50
job.save_meta()

# sqlery — refactor: pass job context explicitly via task_kwargs
# The job ID is available as a kwarg if you enqueue with job_id= or read
# it from the QueuedJob your code already has a reference to.
# Functions that call get_current_job() must be refactored to accept
# job_id as a parameter instead:
def my_task(job_id: int | None = None):
    if job_id:
        from sqlery.django_sqlery.models import QueuedJob
        job = QueuedJob.objects.get(pk=job_id)
```

> **Affects**: any helper that calls `get_current_job()` — e.g. `requeue_if_jobs_pending`, `is_final_retry`. These must be refactored to accept a job reference as an argument.

---

### 2. `job.meta` dict — No equivalent

RQ stores a free-form dict on every job in Redis. sqlery has no `meta` field.

```python
# RQ
job.meta['user_id'] = 42
job.save_meta()
fetched = Job.fetch(job_id, connection=redis)
fetched.refresh()
print(fetched.meta['user_id'])

# sqlery — option A: use tags (list of strings, stored on QueuedJob)
job = enqueue(fn, tags=['user:42'])

# sqlery — option B: pass context through task_kwargs
job = enqueue(fn, user_id=42)  # lands in kwargs the function receives
```

---

### 3. Custom job IDs

RQ accepts arbitrary string IDs; the ID must match `[A-Za-z0-9_-]+`. sqlery uses auto-generated UUID7 integer IDs. String job names are not supported on `QueuedJob`.

```python
# RQ
job = q.enqueue(fn, job_id='my-custom-id-123')
Job.fetch('my-custom-id-123', connection=redis)

# sqlery — use the integer PK returned by enqueue()
job = enqueue(fn)
job_pk = job.pk  # store this to look up later
QueuedJob.objects.get(pk=job_pk)
```

> If you need human-readable identifiers, add a `name` field to your task model and query by that instead.

---

### 4. `enqueue_in()` vs `scheduled_at`

`Queue.enqueue_in(timedelta, fn)` in RQ (and django-tasks-scheduler) schedules a delayed job. sqlery's Queue has `enqueue_in()` and `enqueue_at()` too — this is a direct swap.

```python
# RQ / django-tasks-scheduler
from datetime import timedelta
job = q.enqueue_in(timedelta(minutes=30), fn, arg)

# sqlery — identical API on sqlery's Queue
from sqlery.django_sqlery.queue import Queue
q = Queue('default')
job = q.enqueue_in(timedelta(minutes=30), fn, arg)

# or compute explicitly with enqueue()
from datetime import datetime, timedelta, UTC
job = enqueue(fn, arg, scheduled_at=datetime.now(UTC) + timedelta(minutes=30))
```

---

### 5. `Retry(max=N, interval=secs)` → `max_retries` / `retry_backoff`

RQ's `Retry` class is a first-class kwarg to `enqueue()`. sqlery uses flat kwargs instead.

```python
# RQ
from rq.job import Retry
job = q.enqueue(fn, retry=Retry(max=3, interval=5))
# interval can also be a list for escalating delays:
job = q.enqueue(fn, retry=Retry(max=3, interval=[5, 10, 20]))

# sqlery — flat kwargs on enqueue() or Queue.enqueue()
job = enqueue(fn, max_retries=3, retry_backoff=5.0)

# sqlery compat shim — if you want to keep the Retry class in your code:
from sqlery.compat.scheduler import Retry
job = enqueue(fn, retry=Retry(max=3, interval=5))  # compat layer translates this
```

> sqlery's `retry_backoff` is an exponential multiplier (`backoff * 2^attempt`), not a fixed interval list. For escalating delays use increasing `retry_backoff` values or pre-compute `scheduled_at`.

---

### 6. Admin dashboard URL

django-tasks-scheduler registers its UI at `/admin/scheduler/`. sqlery registers at `/admin/sqlery/`.

```python
# django-tasks-scheduler — urls.py
path('admin/scheduler/', include('scheduler.urls')),

# sqlery — urls.py (already wired via app's AppConfig)
# Dashboard is at /admin/sqlery/ — no extra url conf needed if
# 'sqlery.django_sqlery' is in INSTALLED_APPS.
# For the standalone stats/JSON API:
path('sqlery/', include('sqlery.django_sqlery.urls')),
```
