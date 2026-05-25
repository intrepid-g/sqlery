# Polling Domain Tables: ScheduledTask vs. Custom tick_loop

> Guidance for projects that use **sqlery** and need to periodically scan their
> own domain tables and feed work into the queue.

## The confusion: "sqlery isn't self-sufficient like Celery/RQ"

A common instinct, but not quite right. **None of Celery, RQ, or sqlery
auto-discovers work in your domain tables.** They all run *queued* jobs. If you
want "look at table X every N seconds and enqueue jobs for new rows," you write
that loop yourself in all three:

| Concern                              | Celery                        | RQ                          | sqlery                                |
| ------------------------------------ | ----------------------------- | --------------------------- | ------------------------------------- |
| Execute queued jobs                  | workers                       | workers                     | daemon / workers                      |
| Cron-style scheduled task            | celery beat                   | rq-scheduler (separate pkg) | built-in `ScheduledTask`              |
| **Poll a domain table + enqueue**    | **you write a periodic task** | **you write a cron job**    | **you register a `ScheduledTask`**    |

A custom `tick_loop` (management command or script with `while True: tick(); sleep(n)`)
is the right *shape*. The only question is what runs it. Sqlery already ships
the runner you need: `ScheduledTask`.

## When you need this pattern

Any time the trigger for work lives in your own tables, not in code that calls
`.delay()` directly. Typical signals:

- A "status" or "state" column with a pending value to be advanced.
- An `outbox`/`events` table that needs to be drained to an external system.
- A table populated by an external integration (inbound webhooks, uploads,
  third-party callbacks) where no code path in your app can `.enqueue()` at
  arrival time.
- Reconciliation: rows that timed out, got stuck, or need periodic re-checking.

If the trigger is "my code just did something and now wants a background job,"
you don't need this — just call `my_job.delay(...)` directly.

## The pattern

Split the work into two layers:

1. **A plain Python function** ("the tick") that does a bounded
   `SELECT ... FOR UPDATE SKIP LOCKED`, decides what to do with each row, and
   either advances the row's state inline or calls `some_job.delay(...)` to
   hand off heavier work to the queue.
2. **A `ScheduledTask`** registered with sqlery that calls that function on a
   cron schedule.

Sqlery's daemon runs the scheduler in-process, so you don't need a separate
beat / external cron / HTTP-tick component.

## Recipe

### 1. Write the tick as a plain importable function

```python
# myapp/ticks.py
from django.db import transaction
from myapp.models import Thing
from myapp.jobs import process_thing  # an @job-decorated function

def tick_things(batch_size: int = 50) -> int:
    """Claim pending Thing rows and hand them off to the queue."""
    enqueued = 0
    with transaction.atomic():
        rows = (
            Thing.objects
            .select_for_update(skip_locked=True)
            .filter(status="pending")
            .order_by("created_at")[:batch_size]
        )
        for row in rows:
            row.status = "claimed"
            row.save(update_fields=["status"])
            process_thing.delay(row.id)
            enqueued += 1
    return enqueued
```

Standalone / SQLModel equivalent:

```python
# myapp/ticks.py
from sqlmodel import select
from sqlery.fastapi_sqlery.database import get_session
from myapp.models import Thing
from myapp.jobs import process_thing

def tick_things(batch_size: int = 50) -> int:
    enqueued = 0
    with get_session() as session:
        stmt = (
            select(Thing)
            .where(Thing.status == "pending")
            .order_by(Thing.created_at)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        for row in session.exec(stmt):
            row.status = "claimed"
            session.add(row)
            process_thing.delay(row.id)
            enqueued += 1
        session.commit()
    return enqueued
```

**Rules of thumb for any tick function:**

- **Importable by dotted path** (`myapp.ticks.tick_things`) — sqlery resolves it
  the same way it resolves any `@job`.
- **Bounded** — always `LIMIT batch_size` and `SKIP LOCKED`. The tick may
  overlap with itself under load; never assume a single runner.
- **Idempotent at the row level** — moving a row from `pending` to `claimed`
  inside the same transaction as the `.delay()` is what prevents double
  enqueueing.
- **Cheap per call** — do the SELECT + state transition + `.delay()` here.
  Do the actual work inside the per-row `@job`, not in the tick.
- **No long-running I/O** — the tick is a periodic check, not a worker.

### 2. Register a ScheduledTask

```python
from sqlery.core.scheduler import Scheduler
from sqlery.compat import get_backend

scheduler = Scheduler(backend=get_backend())

scheduler.register_scheduled_task(
    name="things-tick",
    task_path="myapp.ticks.tick_things",
    cron_expression="* * * * *",   # every minute (finest cron granularity)
    queue_name="default",
)
```

Run this once — at deploy time, in a data migration, or in an idempotent app
startup hook. It writes a row into `sqlery_scheduled_task`; the daemon picks it
up automatically.

### 3. Run the sqlery daemon

The daemon runs both the scheduler and the workers. One process, no extra cron,
no HTTP tick endpoints.

```bash
# Django mode
python manage.py daemon

# Standalone mode
sqlery-daemon
```

Each cron firing promotes the tick to a `QueuedJob`; a worker claims and
executes it; the tick body enqueues per-row jobs that workers also claim.

## Sub-minute polling

`ScheduledTask.cron_expression` is standard cron — minimum granularity is one
minute. If you need faster, you have two practical options:

1. **Self-fanout:** at the end of the tick, schedule itself again with a short
   delay (e.g. `tick_things.enqueue_in(seconds=10)`). Arbitrary interval, but
   loses the declarative cron model and you have to bootstrap the first run.
2. **Bigger batches at 1-minute cron:** raise `batch_size`. Fewer transactions,
   usually the same end-to-end throughput. Prefer this unless you have a
   measured latency requirement that 60s misses.

## When a custom tick loop is still the right call

- **Truly continuous polling** with `LISTEN`/`NOTIFY` or long-poll, where a
  worker is meant to block on the table rather than wake on a schedule.
- **Isolation:** the tick consumes resources that should not share the queue
  worker pool (heavy I/O, large memory, external SDKs you don't want loaded in
  every worker). In that case, keep the external runner, but treat it as
  application infrastructure — not a sqlery gap.

For everything else, `ScheduledTask` is the supported, on-rails answer.

## Anti-patterns

- **Doing the actual work inside the tick.** The tick should be a dispatcher.
  Keep heavy logic in per-row `@job`s so failures, retries, and concurrency are
  handled by sqlery, not by your loop.
- **Re-enqueueing the same row twice.** Always transition the row's state in
  the same transaction as `.delay()`, and filter the tick query on the
  pre-transition state.
- **No `SKIP LOCKED`.** Without it, two ticks running concurrently (e.g.
  across deploys or restarts) will serialize on row locks and stall.
- **Unbounded `SELECT`.** Always cap with `batch_size`. A single tick run
  should be fast and finite.
- **Custom `while True: sleep(...)` in a management command** when a 1-minute
  cron tick would do. You inherit signal handling, restart safety, deployment,
  and observability problems you don't need.

## TL;DR

- "Sqlery isn't self-sufficient like Celery" is a misread — Celery and RQ have
  the same gap; they just label the periodic runner differently.
- For any "scan my table on an interval and enqueue work" need, write a small
  tick function and register it as a `ScheduledTask`. Run `sqlery-daemon`.
  No custom loop, no external cron, no HTTP tick endpoints.
