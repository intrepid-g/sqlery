# sqlery

**A job queue and cron scheduler for Python that runs on the database you already have. No Redis, no broker.**

[![Tests](https://github.com/intrepid-g/sqlery/actions/workflows/test.yml/badge.svg)](https://github.com/intrepid-g/sqlery/actions/workflows/test.yml)
[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

sqlery stores jobs, results and schedules in PostgreSQL or SQLite. It has a Celery/RQ-style API (`@job`, `.delay()`, `apply_async()`, `chain`, `group`, `chord`) and works in two modes with the same job API:

- **Django**: an app with ORM models, migrations, management commands and an admin dashboard.
- **Standalone**: SQLAlchemy/SQLModel, a `sqlery-worker` CLI and a FastAPI dashboard. Use it with FastAPI, Flask or plain scripts.

Status: beta (0.28). Read the [CHANGELOG](CHANGELOG.md) upgrade notes before each upgrade.

## Why sqlery

- **One less service.** The database is the queue, the result store and the scheduler. There is no broker to deploy, monitor or back up.
- **Enqueue inside your transaction (Django).** A job enqueued in `transaction.atomic()` commits or rolls back with your data. There is no "job ran before the row existed" bug.
- **Jobs are rows.** You can inspect the queue with SQL, the dashboard or the ORM.
- **Easy to move to.** Celery-style and RQ-style calls work, and shims exist for RQ and django-tasks-scheduler imports.
- **Starts small.** SQLite for dev, tests and a single node. PostgreSQL for several workers or hosts.

When not to use it: if you need thousands of jobs per second, or exchanges and routing keys, use a broker. See [How it compares](#how-it-compares).

## Quickstart

Requires Python 3.13+.

### Django

```bash
pip install "sqlery[django]" "psycopg[binary]"
```

`psycopg` is needed even on SQLite, because the migrations import `django.contrib.postgres`.

```python
# settings.py
INSTALLED_APPS += ["sqlery.django_sqlery", "myapp"]

DJANGO_SQL_JOBS = {
    "WORKER_QUEUES": ["default"],
    "TRIGGER_MODE": "disabled",  # jobs run only in the worker started below
}
```

```python
# myapp/tasks.py
from sqlery import job


@job(queue="default", max_retries=2, timeout=30)
def add(x, y):
    return x + y
```

```bash
python manage.py migrate
python manage.py daemon start        # worker pool + scheduler, runs in the foreground
```

Enqueue from a view, a signal or `python manage.py shell`:

```python
from myapp.tasks import add

result = add.apply_async(args=(20, 22))
result.get(timeout=30)   # 42
```

Workers preload the `tasks` module of each installed app. Your settings must define `BASE_DIR` (the default `startproject` layout does): the daemon keeps its pid and log files in `BASE_DIR/tmp`.

### Standalone

```bash
pip install "sqlery[standalone]"
```

```python
# tasks.py
from sqlery import job


@job(queue="default", max_retries=2, timeout=30)
def add(x, y):
    return x + y
```

```python
# enqueue.py
from sqlery.compat import initialize

initialize(database_url="sqlite:///jobs.db")   # creates the tables; call once per process

import tasks

result = tasks.add.apply_async(args=(20, 22))
print(result.get(timeout=30))   # 42, after the worker runs the job
```

```bash
# terminal 1
export SQLERY_DATABASE_URL=sqlite:///jobs.db PYTHONPATH=.
sqlery-worker --queues default

# terminal 2
python enqueue.py
```

For PostgreSQL, use `postgresql://user:password@host/dbname` and add the `postgres` extra.

### Three rules for jobs

1. Define jobs at module level in a module the worker can import. Jobs are stored by dotted path.
2. Pass JSON-friendly arguments: ids and ISO strings, not model instances, `set` or `Decimal`.
3. Make jobs idempotent. Delivery is at-least-once: a job on a crashed worker runs again.

## Features

| Area | What you get |
|---|---|
| Enqueue | `.enqueue()`, `.delay()`, `apply_async(countdown=, eta=, expires=)`, `enqueue_at()`, or `enqueue("pkg.mod.func", ...)` by dotted path |
| Results | `AsyncResult` with `.state` and `.get(timeout=)`. It follows retries. |
| Retries | `max_retries`, `retry_backoff`, `retry_backoff_max`, `retry_jitter`, `autoretry_for`, `bind=True` |
| Time limits | Hard `timeout` (the job is killed) and catchable `soft_time_limit` |
| Workflows | `chain`, `group`, `chord`, and job dependencies (`depends_on`) |
| Scheduling | Cron, interval and one-off tasks. Solar schedules (`sunrise`, `sunset`, ...) |
| Queues | Named queues, per-job `priority`, tag concurrency limits and tag rate limits |
| Control | `revoke(job_id)`, and `revoke(job_id, terminate=True)` for a running job |
| Workers | Prefork pool (default) or fork-per-job, child recycling by job count or memory, DB reconnect with backoff |
| Recovery | Fenced claims, heartbeats and leases; jobs of dead workers are reclaimed |
| Signals | `task_prerun`, `task_postrun`, `task_success`, `task_failure`, `task_retry` |
| Webhooks | Optional completion webhook per job, with SSRF protection |
| Operations | Retention cleanup, job and task export/import, health endpoint, dashboard |
| PostgreSQL | `FOR UPDATE SKIP LOCKED` claiming, optional LISTEN/NOTIFY wake-ups, partitioned tables |

A scheduled task in Django:

```python
from sqlery.django_sqlery.models import ScheduledTask

ScheduledTask.objects.create(
    name="nightly-add",
    task_path="myapp.tasks.add",
    task_kwargs={"x": 1, "y": 1},
    schedule_type="cron",          # or "interval", "once"
    cron_expression="0 2 * * *",
    queue_name="default",
)
```

The daemon enqueues it when it is due.

## Execution modes

A long-running worker is the default for production. The other modes are for hosts where no process can stay up. In Django you select them with `TRIGGER_MODE` and `EXECUTION_MODE` in `DJANGO_SQL_JOBS`.

| Mode | How to start it | Use it when |
|---|---|---|
| Daemon | `python manage.py daemon start` (Django), `sqlery daemon start` (standalone) | Production. Worker pool, scheduler and maintenance in one command. |
| Standalone worker | `sqlery-worker --queues default,high` | Non-Django services. |
| One-shot | `python manage.py run_jobs --max-jobs 10` | System cron, CI, or a platform that only runs short commands. |
| Subprocess | `TRIGGER_MODE = "subprocess"` | No daemon can run. The web process starts short-lived workers. |
| Thread / middleware | `TRIGGER_MODE = "middleware"`, `EXECUTION_MODE = "thread"` | Development only. Jobs run in the web process. |
| HTTP trigger | `TRIGGER_MODE = "http"` + `INTERNAL_BASE_URL`, `INTERNAL_SECRET` | ASGI or serverless hosts. An HMAC-signed request starts a worker. |
| Lambda / EventBridge | `TRIGGER_MODE = "eventbridge"`, handler `sqlery.lambda_handler.handler` | **Experimental.** Not production-ready. See [examples/lambda](examples/lambda/README.md). |

`TRIGGER_MODE` defaults to `"middleware"`. If you run a daemon or worker, set it to `"disabled"` as in the quickstart.

The daemon uses one worker process per CPU by default. Each worker uses about 75 MiB plus its job child. On small hosts, set `--workers N` (Django) or `SQLERY_WORKER_CONCURRENCY`. Restart the daemon after a deploy: workers run the code that was loaded at start.

[examples/run_modes_lab](examples/run_modes_lab/README.md) runs each mode in Docker against PostgreSQL.

## How it compares

| | sqlery | Celery | RQ | django-tasks (database backend) |
|---|---|---|---|---|
| Needs | PostgreSQL or SQLite | A broker (Redis, RabbitMQ, ...) | Redis | Your Django database |
| Works without Django | Yes | Yes | Yes | No |
| Enqueue in your DB transaction | Yes (Django mode) | No | No | Yes |
| Cron scheduling | Built in | Celery beat | Add-on | Not built in |
| Chains, groups, chords | Yes | Yes | Dependencies only | No |
| Dashboard | Built in | Flower (separate) | Separate package | Django admin |
| Throughput | Tens of jobs per second per worker | High | High | Moderate |
| Maturity | Beta | Very mature, large ecosystem | Mature | Young |

Where the others are better:

- **Celery** is faster, far more mature, and has exchanges, routing keys, remote control, autoscaling and pluggable result backends. sqlery has none of these. See [docs/CELERY_PARITY.md](docs/CELERY_PARITY.md) for the full list of what is and is not covered.
- **RQ** is simpler and faster if you already run Redis.
- **django-tasks** follows Django's official Tasks API. If you only need basic background tasks in Django, it is the smaller choice. sqlery can also run with `EXECUTION_MODE = "django-tasks"`.

A database queue costs queries. The changelog measures about 44 no-op jobs per second per worker at concurrency 1 on a local PostgreSQL. Scale with more workers, and move to a broker if that is not enough.

Migration guides: [from Celery](docs/MIGRATION_FROM_CELERY.md), [from RQ](docs/MIGRATION_FROM_RQ.md), [from django-tasks-scheduler](docs/MIGRATION_FROM_DJANGO_TASKS_SCHEDULER.md). The drop-in shims are `sqlery.compat.rq` and `sqlery.compat.scheduler`.

## Dashboard

**Django.** Add the URLs, then open `/admin/sqlery/` as a staff user:

```python
# urls.py
urlpatterns = [
    path("", include("sqlery.django_sqlery.urls")),
    path("admin/", admin.site.urls),
]
```

It shows queues, jobs, scheduled tasks and workers, and lets you stop, retry, reprioritize and enqueue jobs. A health check is at `/_internal/health`.

**Standalone.** Serve the FastAPI app after `initialize()`:

```python
# dashboard.py
import uvicorn
from sqlery.compat import initialize

initialize(database_url="sqlite:///jobs.db")

from sqlery.fastapi_sqlery.app import app

uvicorn.run(app, host="127.0.0.1", port=8000)
```

Pages: `/`, `/jobs`, `/scheduled-tasks`, `/workers`, `/registries`. A JSON API is under `/api/`. Every request except `/healthz` needs the header `X-Sqlery-Key`. The key comes from `SQLERY_DASHBOARD_API_KEY`, or is generated in `.sqlery/dashboard.key` on first start. When you mount the app inside your own ASGI app, it trusts your app's authentication (`SQLERY_DASHBOARD_AUTH=inherit`). `SQLERY_DASHBOARD_AUTH=disabled` turns the check off; use that only on a trusted network.

## Documentation

- [llms.txt](llms.txt): the verified API summary. Start here, also if you are an AI agent.
- [CHANGELOG.md](CHANGELOG.md): current behavior and upgrade notes.
- [docs/CELERY_PARITY.md](docs/CELERY_PARITY.md): results, retries, canvas, revoke, signals, time limits, solar schedules.
- [docs/CONFIGURATION.md](docs/CONFIGURATION.md) and [docs/ENVIRONMENT_VARIABLES.md](docs/ENVIRONMENT_VARIABLES.md): settings and `SQLERY_*` variables.
- [docs/SECURITY.md](docs/SECURITY.md): dashboard authentication, webhook SSRF protection, the `ALLOWED_TASK_MODULES` allowlist.
- [docs/design/queue-robustness.md](docs/design/queue-robustness.md): claiming, the ready queue, partitions, recovery.
- [docs/guides/scheduled-tasks-vs-tick-loop.md](docs/guides/scheduled-tasks-vs-tick-loop.md): when to use a scheduled task.
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md): common problems.
- Examples: [job dependencies](examples/job_dependencies_usage.md), [rate limiting](examples/rate_limiting_usage.md), [tag concurrency](examples/tag_concurrency_usage.md), [webhooks](examples/webhooks_usage.md).

Some older docs are behind the code. If a doc and the source disagree, the source is correct; [llms.txt](llms.txt) lists the known stale pages.

## Contributing

```bash
git clone https://github.com/intrepid-g/sqlery.git
cd sqlery
uv pip install -e ".[dev]"
uv run pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md). Report bugs in the [issue tracker](https://github.com/intrepid-g/sqlery/issues).

## License

MIT. See [LICENSE](LICENSE).
