# Celery parity

What Sqlery offers compared with Celery, with a short example of each. Everything here works in
both Django and standalone modes, on SQLite and PostgreSQL, and needs no broker: the database
is the queue, the result store and the scheduler.

| Celery | Sqlery | Notes |
| --- | --- | --- |
| `task.delay()` / `apply_async()` | `job.delay()` / `job.apply_async()` | `apply_async` returns an `AsyncResult` |
| `AsyncResult` (`state`, `get()`) | `sqlery.AsyncResult` | follows retries; `REVOKED` state |
| `countdown` / `eta` / `expires` | `apply_async(countdown=, eta=, expires=)` | `expires` maps to the job TTL |
| `autoretry_for`, `retry_backoff`, `retry_jitter`, `self.retry()` | `@job(autoretry_for=, retry_backoff_max=, retry_jitter=, bind=True)` | needs `max_retries > 0` |
| `chain`, `group`, `chord` | `sqlery.chain`, `group`, `chord` | built on job dependencies |
| `soft_time_limit` / `time_limit` | `@job(soft_time_limit=, timeout_seconds=)` | soft is catchable, hard kills |
| Signals (`task_prerun`, ...) | `sqlery.core.signals` | `prerun`, `postrun`, `success`, `failure`, `retry` |
| `revoke(terminate=True)` | `sqlery.revoke(job_id, terminate=True)` | works across hosts |
| `beat` crontab / interval | `ScheduledTask` cron / interval / once | built in |
| `beat` solar | `solar:sunset 40.71 -74.00 -15m` | in the cron field |
| Task rate limits / routing | tag limits + named queues | `TAG_RATE_LIMITS`, `TAG_CONCURRENCY_LIMITS` |
| Priority queues | `priority=` | higher runs first |
| `--pool=prefork`, `worker_max_tasks_per_child`, `worker_max_memory_per_child` | `WORKER_POOL="prefork"`, `WORKER_MAX_TASKS_PER_CHILD`, `WORKER_MAX_MEMORY_PER_CHILD` | same defaults; `"fork"` = RQ's fork-per-job |
| `CELERY_DB_REUSE_MAX` | `WORKER_DB_REUSE_MAX` | defaults to the child recycle rate |

Not provided: exchanges and routing keys, Flower-style remote control (ping, pool grow/shrink,
broadcast), per-task `prefetch` or autoscale, and a pluggable result backend (results live in
the job row).

## Results

```python
from sqlery import job

@job(queue="default")
def add(a, b):
    return a + b

res = add.apply_async(args=(2, 3), countdown=10, expires=300)
res.state        # PENDING -> STARTED -> SUCCESS | FAILURE | REVOKED
res.ready()
res.get(timeout=60)                  # 5
res.get(timeout=60, propagate=False) # returns the error text instead of raising
```

`get()` raises `TimeoutError`, `sqlery.core.result.TaskFailedError` (with `.traceback`) or
`TaskRevokedError`. Task arguments are passed explicitly (`args=`, `kwargs=`), so option names can
never collide with your task's own keyword arguments. `.delay()` and `.enqueue()` behave as before.

Results are stored JSON-encoded in the job's `output` column (strings verbatim). A task returning
a string that is itself valid JSON, such as `"123"`, comes back from `get()` as `123`; read
`res.output` for the raw text.

## Retries

```python
from sqlery import job
from sqlery.core.retry import Retry

@job(max_retries=5, retry_backoff=2, retry_backoff_max=300, retry_jitter=True,
     autoretry_for=(ConnectionError, TimeoutError))
def fetch(url): ...

@job(bind=True, max_retries=3)
def upload(self, path):
    try:
        ...
    except ConnectionError as exc:
        self.retry(exc=exc, countdown=30)       # or: raise Retry(exc, countdown=30)
    print(self.request.retries)
```

Without `autoretry_for`, any exception retries while attempts remain (the previous behaviour).
`RETRY_BACKOFF_MAX` and `RETRY_JITTER` set the same options globally. A retry keeps the original
job's tags, metadata, callbacks, timeout and dependencies.

## Workflows

```python
from sqlery import chain, group, chord

(add.s(1, 2) | add.s(10) | add.si(0)).apply_async()    # 3 -> 13 -> 0 (.si ignores the input)
group(add.s(i, i) for i in range(5)).apply_async().get()
chord(group(add.s(i, 1) for i in range(3)), total.s()).apply_async().get()   # total([1, 2, 3])
chain(first.si(), group(a.s(), b.s()), merge.s())      # a group followed by a step is a chord
```

A step runs once its dependencies succeed and receives their results as the leading argument
(`.si()` opts out). A failed step, after its retries, fails everything downstream. All jobs of a
workflow are created up front without a cross-job transaction; if creating one fails part-way,
the earlier ones stay queued.

## Signals

```python
from sqlery.core.signals import task_failure

@task_failure.connect                                   # or connect(sender="myapp.tasks.fetch")
def alert(sender, job=None, exc=None, traceback=None, **kw):
    notify(f"{sender} failed: {exc}")
```

Receivers run in the process executing the job (the forked child under a worker), so register
them in code the worker imports. A receiver that raises is logged and never fails the job.

## Time limits

```python
from sqlery.core.execution import SoftTimeLimitExceeded

@job(soft_time_limit=50, timeout_seconds=60)
def report():
    try:
        ...
    except SoftTimeLimitExceeded:
        save_partial_results()
```

After 50 seconds `SoftTimeLimitExceeded` is raised inside the task; at 60 the hard limit stops it.
Both use `SIGALRM`, so they work on Unix in the main thread of the executing process.

## Revoke

```python
from sqlery import revoke

revoke(job_id)                       # queued/scheduled: cancelled
revoke(job_id, terminate=True)       # running: the worker kills it
add.apply_async(args=(1, 2)).revoke()
```

A terminate request is a flag the worker running the job polls (`REVOKE_POLL_INTERVAL`, default 2
seconds, `0` disables), so it works across hosts. The job's process group is killed and the job is
marked failed with no retry. Only jobs run by a `WorkerProcess` (daemon or `sqlery-worker`) can be
interrupted; inline execution (HTTP trigger, Lambda, direct `TaskExecutor`) cannot.

## Solar schedules

```python
from sqlery.solar import solar_spec

ScheduledTask.objects.create(
    name="evening report", task_path="myapp.tasks.report", schedule_type="cron",
    cron_expression=solar_spec("sunset", 40.7128, -74.0060, offset_minutes=-15),
)   # "solar:sunset 40.7128 -74.006 -15m"
```

Events are `sunrise`, `sunset`, `solar_noon`, `dawn` and `dusk` (civil twilight). Latitude is
positive north, longitude positive east, and the offset takes `s`, `m` or `h`. Times are computed in
UTC with the standard sunrise equation (about a minute of accuracy, no extra dependency). On
polar days and nights the next day the event occurs is used.

## Tag limits

```python
# settings.py (Django) or initialize(...) config
DJANGO_SQL_JOBS = {"TAG_CONCURRENCY_LIMITS": {"stripe": 2}, "TAG_RATE_LIMITS": {"stripe": "100/m"}}

@job(tags=["stripe"])
def charge(...): ...
```

At most 2 `stripe` jobs run at once, and at most 100 start per minute. Other queued jobs are
never blocked behind a limited one. On SQLite the check is best-effort under heavy concurrency; on
PostgreSQL it is serialised per tag.

## Settings added

| Setting | Default | Purpose |
| --- | --- | --- |
| `RETRY_BACKOFF_MAX` | none | cap on the retry delay (seconds) |
| `RETRY_JITTER` | `False` | randomise the retry delay |
| `WORKER_POOL` | `"prefork"` | `"fork"` forks a child per job (RQ) |
| `WORKER_MAX_TASKS_PER_CHILD` | none | recycle the prefork child after N jobs |
| `WORKER_MAX_MEMORY_PER_CHILD` | none | recycle it once its max RSS exceeds N KiB |
| `WORKER_DB_REUSE_MAX` | none | force-refresh DB connections every N jobs (none = the recycle rate) |
| `DB_RECONNECT_BACKOFF_BASE` / `_MAX` | `0.5` / `30` | reconnect backoff (seconds, full jitter) after a lost DB connection |
| `REVOKE_POLL_INTERVAL` | `2.0` | seconds between terminate-flag checks (`0` disables) |
| `LOG_TASK_ARGUMENTS` | `False` | log task argument values (they can contain secrets) |
| `REQUIRE_BODY_SIGNATURE` | `False` | reject timestamp-only trigger signatures |
| `LAMBDA_SHARED_SECRET` | none | require signed Lambda events (`lambda_core.sign_event`) |
| `LAMBDA_SIGNATURE_MAX_AGE` | `300` | seconds a signed Lambda event stays valid |

See also [MIGRATION_FROM_CELERY.md](MIGRATION_FROM_CELERY.md) and [SECURITY.md](SECURITY.md).
