# Celery → sqlery Migration Guide (Agent-Optimized)

## Concept Mapping

| Celery | sqlery |
|--------|--------|
| `@app.task` decorator | plain function + `enqueue(fn, ...)` |
| `celery.app` / `Celery()` | `initialize()` (standalone) or Django settings |
| `task.delay(args)` | `enqueue(fn, *args)` |
| `task.apply_async(kwargs, countdown=N)` | `enqueue(fn, delay_seconds=N)` |
| `beat` (periodic tasks) | `ScheduledTask` (cron, built-in) |
| `celery worker` | `sqlery daemon start` |
| `celery beat` | built-in daemon scheduler |
| `AsyncResult` | `QueuedJob` / `job.status` |
| Broker (Redis/RabbitMQ) | PostgreSQL or SQLite |
| Result backend | same DB (no separate backend) |
| `task.retry()` | `max_retries=` + `retry_backoff=` on enqueue |
| `chord` / `chain` / `group` | not supported |

---

## Common Patterns: Before → After

### 1. Define and call a task

```python
# Celery
from celery import Celery
app = Celery('tasks', broker='redis://localhost')

@app.task
def add(x, y):
    return x + y

add.delay(4, 4)

# sqlery
from sqlery import enqueue

def add(x, y):
    return x + y

enqueue(add, x=4, y=4)
# or by path: enqueue('myapp.tasks.add', x=4, y=4)
```

### 2. Enqueue with delay / ETA

```python
# Celery
add.apply_async(args=[4, 4], countdown=60)
add.apply_async(args=[4, 4], eta=datetime(2025, 1, 1, 12, 0))

# sqlery
from sqlery import enqueue
enqueue(add, x=4, y=4, delay_seconds=60)
enqueue(add, x=4, y=4, scheduled_at=datetime(2025, 1, 1, 12, 0))
```

### 3. Retry on failure

```python
# Celery
@app.task(bind=True, max_retries=3, default_retry_delay=60)
def my_task(self):
    try:
        do_something()
    except Exception as exc:
        raise self.retry(exc=exc, countdown=60)

# sqlery
from sqlery import enqueue
enqueue(my_task, max_retries=3, retry_backoff=60.0)
# Retries happen automatically on exception
```

### 4. Periodic task (beat equivalent)

```python
# Celery beat
app.conf.beat_schedule = {
    'add-every-30-seconds': {
        'task': 'tasks.add',
        'schedule': 30.0,
        'args': (16, 16)
    },
}

# sqlery (Django)
from sqlery.django_sqlery.models import ScheduledTask
ScheduledTask.objects.create(
    name='add-every-minute',
    task_path='myapp.tasks.add',
    cron_expression='* * * * *',
    queue_name='default',
    enabled=True,
)

# sqlery (standalone)
from sqlery import schedule_task
schedule_task('myapp.tasks.add', cron='* * * * *', name='add-every-minute')
```

### 5. Initialization

```python
# Celery
app = Celery('proj', broker='redis://localhost/0', backend='redis://localhost/0')
app.config_from_object('django.conf:settings', namespace='CELERY')

# sqlery (Django) — settings.py
SQLERY = {
    'MAX_WORKERS_PER_NODE': 4,
    'WORKER_QUEUES': ['high', 'default', 'low'],
    'AUTO_CLEANUP_JOBS': True,
}

# sqlery (standalone)
from sqlery import initialize
initialize(
    database_url='postgresql://localhost/mydb',
    max_workers=4,
    worker_queues=['high', 'default', 'low'],
)
```

---

## Configuration Mapping

| Celery setting | sqlery equivalent |
|----------------|-------------------|
| `broker_url` | `database_url` (no broker needed) |
| `result_backend` | same DB automatically |
| `worker_concurrency` | `MAX_WORKERS_PER_NODE` |
| `task_default_queue` | `DEFAULT_QUEUE` |
| `task_queues` | `WORKER_QUEUES` list |
| `task_soft_time_limit` | `timeout_seconds=` on `enqueue()` |
| `result_expires` | `JOB_RETENTION.success.max_age_days` |
| `task_max_retries` | `DEFAULT_MAX_RETRIES` |

---

## CLI Mapping

| Celery | sqlery |
|--------|--------|
| `celery -A proj worker` | `sqlery daemon start` |
| `celery -A proj beat` | built-in (same daemon) |
| `celery -A proj status` | `sqlery status` |
| `celery -A proj inspect active` | `sqlery workers` |
| `celery -A proj purge` | — (cancel via admin or API) |
| `flower` | Django admin dashboard (`/admin/sqlery/`) |

---

## Unsupported / Differences

- **No primitives**: `chord`, `chain`, `group`, `canvas` — no task composition
- **No `self.request`**: tasks are plain functions, no task context object
- **No `bind=True`**: no self-reference in tasks
- **No signals**: no `task_prerun`, `task_postrun`, `task_failure` signals
- **No custom serializers**: args/kwargs stored as JSON; use JSON-serializable types only
- **No routing by content**: queue routing is by name + priority only
- **No ETA precision**: scheduled_at is stored in DB; resolution is daemon check interval (default 10s)
- **Workers are processes**: each worker is a subprocess (not threads/greenlets)
