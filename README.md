# sqlery

**A job queue and cron scheduler for Python that runs on the database you already have. No Redis, no broker.**

[![Tests](https://github.com/intrepid-g/sqlery/actions/workflows/test.yml/badge.svg)](https://github.com/intrepid-g/sqlery/actions/workflows/test.yml)
[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

sqlery stores jobs, results and schedules in PostgreSQL or SQLite. It has a Celery/RQ-style API (`@job`, `.delay()`, `apply_async()`, `chain`, `group`, `chord`). It works in two modes with the same job API:

- **Django**: an app with ORM models, migrations, management commands and an admin dashboard.
- **Standalone**: SQLAlchemy/SQLModel, a `sqlery-worker` CLI and a FastAPI dashboard. Use it with FastAPI, Flask or plain scripts.

Status: beta (0.28). Read the [CHANGELOG](CHANGELOG.md) upgrade notes before each upgrade.

## Why sqlery

Use it when:

- **You want one less service.** The database is the queue, the result store and the scheduler. There is no broker to deploy, monitor or back up.
- **You enqueue inside a transaction (Django).** A job enqueued in `transaction.atomic()` commits or rolls back with your data. There is no "job ran before the row existed" bug.
- **You want jobs as rows.** Inspect the queue with SQL, the dashboard or the ORM.
- **You come from Celery or RQ.** Celery-style and RQ-style calls work. Shims exist for RQ and django-tasks-scheduler imports.
- **You start small.** SQLite for dev, tests and a single node. PostgreSQL for several workers or hosts.

Do not use it when:

- You need thousands of jobs per second. A database queue costs queries. Use a broker.
- You need exchanges, routing keys, remote control or autoscaling. Use Celery.
- You need a 1.0 API today. sqlery is beta and upgrades can need a migration.
- Your database is not PostgreSQL or SQLite.

## Install

sqlery is not on PyPI yet. A PyPI release is planned. Until then, install from GitHub. Requires **Python 3.13+**.

```bash
# uv
uv add "sqlery[standalone] @ git+https://github.com/intrepid-g/sqlery.git@v0.28.0"
uv add "sqlery[django] @ git+https://github.com/intrepid-g/sqlery.git@v0.28.0" "psycopg[binary]"

# pip
pip install "sqlery[standalone] @ git+https://github.com/intrepid-g/sqlery.git@v0.28.0"
pip install "sqlery[django] @ git+https://github.com/intrepid-g/sqlery.git@v0.28.0" "psycopg[binary]"
```

Always pin a release tag, as above. Do not track `main`: it can contain unreleased schema changes. The tags are on the [releases page](https://github.com/intrepid-g/sqlery/releases).

| Extra | Adds |
|---|---|
| `standalone` | SQLModel, FastAPI, uvicorn, Typer, Alembic. Needed for standalone mode. |
| `django` | Django 5.2+. Needed for Django mode. |
| `postgres` | `psycopg[binary]` 3. It bundles `libpq`, so it needs no system library. For production images that use the system `libpq`, install `psycopg[c]` yourself instead. |
| `http`, `webhooks`, `eventbridge`, `tasks` | `httpx`, `httpx`, `boto3`, `django-tasks`. For the HTTP trigger, webhooks, Lambda and django-tasks modes. |

Django mode needs `psycopg` even on SQLite, because one migration imports `django.contrib.postgres`.

## Quickstart

Both paths enqueue `add(20, 22)`, run it in a worker and print `42`. Both use SQLite, so there is nothing else to install.

### Standalone

```python
# tasks.py
from sqlery import job


@job(queue="default")
def add(x, y):
    return x + y
```

```python
# main.py
from sqlery.compat import initialize

initialize(database_url="sqlite:///jobs.db")  # creates the tables

import tasks

result = tasks.add.apply_async(args=(20, 22))
print("state:", result.state)
print("result:", result.get(timeout=30))
print("state:", result.state)
```

```bash
export SQLERY_DATABASE_URL=sqlite:///jobs.db PYTHONPATH=.
sqlery-worker --queues default &     # or run it in a second terminal
python main.py
```

Output of `main.py`:

```text
state: PENDING
result: 42
state: SUCCESS
```

The worker logs `[CLAIMED] Job #... (tasks.add)` and `[SUCCESS] Job #...`.

### Django

```bash
django-admin startproject demo . && python manage.py startapp myapp
```

```python
# demo/settings.py: add at the end
INSTALLED_APPS += ["sqlery.django_sqlery", "myapp"]

DJANGO_SQL_JOBS = {
    "WORKER_QUEUES": ["default"],
    "TRIGGER_MODE": "disabled",  # jobs run only in the worker started below
}
```

```python
# myapp/tasks.py
from sqlery import job


@job(queue="default")
def add(x, y):
    return x + y
```

```bash
python manage.py migrate
python manage.py daemon start &      # worker pool + scheduler; or run it in a second terminal
python manage.py shell -c "
from myapp.tasks import add
result = add.apply_async(args=(20, 22))
print('result:', result.get(timeout=30))
"
```

Output:

```text
result: 42
```

Enqueue the same way from a view or a signal. Workers preload the `tasks` module of each installed app. Your settings must define `BASE_DIR` (the `startproject` layout does): the daemon keeps its pid and log files in `BASE_DIR/tmp`.

### Three rules for jobs

1. Define jobs at module level in a module the worker can import. Jobs are stored by dotted path.
2. Pass JSON-friendly arguments: ids and ISO strings, not model instances, `set` or `Decimal`.
3. Make jobs idempotent. Delivery is at-least-once: a job on a crashed worker runs again.

## Supported databases

| Database | Status | Use it for |
|---|---|---|
| SQLite | Supported | Dev, tests, a single node. |
| PostgreSQL | Supported. CI runs on PostgreSQL 15. | Production, several workers or hosts. |
| libSQL (embedded file) | **Experimental**, Django mode only | See below. Remote Turso is not supported. |
| MySQL and others | Not supported | |

**SQLite.** URL: `sqlite:///relative.db` or `sqlite:////abs/path.db`. Claiming uses optimistic locking.

**PostgreSQL.** URL: `postgresql://user:password@host/dbname`, plus the `postgres` extra or `psycopg[binary]`. In Django, use the normal `DATABASES` setting. PostgreSQL adds `FOR UPDATE SKIP LOCKED` claiming, optional LISTEN/NOTIFY wake-ups and partitioned tables. On a new standalone database, let one process run `initialize()` before you start the others: two first starts at the same moment can collide while they create the tables.

**libSQL (experimental).** Django mode only, with an embedded libSQL file. This is not remote Turso, and it is not standalone (SQLAlchemy) mode. The `libsql` extra pins `libsql==0.1.11`. It is new after v0.28.0, so install from GitHub and pin the latest release tag (newer than v0.28.0) from the [releases page](https://github.com/intrepid-g/sqlery/releases):

```bash
pip install "sqlery[django,libsql] @ git+https://github.com/intrepid-g/sqlery.git@<latest-tag>" "psycopg[binary]"
```

Then set the engine:

```python
DATABASES = {
    "default": {
        "ENGINE": "sqlery.django_sqlery.libsql_backend",
        "NAME": "/abs/path/queue.db",
        "OPTIONS": {"timeout": 5.0},
    }
}
```

The backend wraps the `libsql` module under Django's SQLite backend, so the vendor stays `sqlite` and the SQLite claiming rules apply. CI runs one real enqueue and one worker run on a libsql file, on Python 3.13 with Django 5.2 and 6.1. Both passed on libsql 0.1.11. Caveats:

- Experimental. The backend is tested for the queue path only. It does not register Django's SQL functions (some date lookups will fail).
- Use `psycopg[binary]` or install libpq: the sqlery migrations import `django.contrib.postgres`.
- No remote Turso URL, no embedded replica, and no standalone-mode driver. `sqlalchemy-libsql` fails in `initialize()` (`foreign key mismatch`) and `django-libsql` 0.1.3 fails on Django 6.1.
- Same limits as SQLite: not for production multi-worker setups.

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
| Recovery | Fenced claims, heartbeats and leases. Jobs of dead workers are reclaimed. |
| Signals | `task_prerun`, `task_postrun`, `task_success`, `task_failure`, `task_retry` |
| Webhooks | Optional completion webhook per job, with SSRF protection |
| Operations | Retention cleanup, job and task export/import, health endpoint, dashboard |
| PostgreSQL | `FOR UPDATE SKIP LOCKED` claiming, optional LISTEN/NOTIFY wake-ups, partitioned tables |

A chain and a group:

```python
from sqlery import chain, group

(add.s(2, 3) | add.s(10)).apply_async().get(timeout=30)              # 15
group(add.s(i, i) for i in range(3)).apply_async().get(timeout=30)   # [0, 2, 4]
```

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
| Django daemon | `python manage.py daemon start` | Production. Worker pool, scheduler and maintenance in one command. |
| Standalone worker | `sqlery-worker --queues default,high` | Non-Django services. |
| Standalone daemon | `DaemonManager().start(detach=False)` after `initialize()` | You also need the scheduler in standalone mode. See the note below. |
| One-shot | `python manage.py run_jobs --worker-only --max-jobs 10`, or `sqlery-worker --max-jobs 10` | System cron, CI, or a platform that only runs short commands. |
| Subprocess | `TRIGGER_MODE = "subprocess"` | No daemon can run. The web process starts short-lived workers. |
| Thread / middleware | `TRIGGER_MODE = "middleware"`, `EXECUTION_MODE = "thread"` | Development only. Jobs run in the web process. |
| HTTP trigger | `TRIGGER_MODE = "http"` + `INTERNAL_BASE_URL`, `INTERNAL_SECRET` | ASGI or serverless hosts. An HMAC-signed request starts a worker. |
| Lambda / EventBridge | `TRIGGER_MODE = "eventbridge"`, handler `sqlery.lambda_handler.handler` | **Experimental.** Not production-ready. See [examples/lambda](examples/lambda/README.md). |

Notes:

- `TRIGGER_MODE` defaults to `"middleware"`. If you run a daemon or worker, set it to `"disabled"` as in the quickstart.
- Standalone daemon: in 0.28 the `sqlery daemon start` command does not read `SQLERY_DATABASE_URL` and stops with "Database not initialized". Start the daemon from a script: call `initialize()`, import your task modules, then call `sqlery.core.daemon.DaemonManager().start(detach=False)`.
- The daemon uses one worker process per CPU by default. Each worker uses about 75 MiB plus its job child. On small hosts, set `--workers N` (Django) or `SQLERY_WORKER_CONCURRENCY`.
- Restart the daemon after a deploy: workers run the code that was loaded at start.
- Stop a foreground daemon with Ctrl+C or SIGTERM.

[examples/run_modes_lab](examples/run_modes_lab/README.md) has a Docker setup for each mode against PostgreSQL. Some of its commands are stale; [llms.txt](llms.txt) lists them.

## Dashboard

**Django.** Add the URLs, then open `/admin/sqlery/` as a staff user:

```python
# urls.py
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("", include("sqlery.django_sqlery.urls")),
    path("admin/", admin.site.urls),
]
```

It shows queues, jobs, scheduled tasks and workers. You can stop, retry, reprioritize and enqueue jobs. A health check is at `/_internal/health`.

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

To open the dashboard in a browser, enter any user name and the dashboard key as the password in the login prompt (HTTP Basic). Use HTTPS, because the key travels with every request.

## How it compares

The sqlery column is checked against the source. The other columns come from each project's public documentation. They were not tested for this table, so check the current docs of each project.

| | sqlery | Celery | RQ | django-tasks (database backend) |
|---|---|---|---|---|
| Needs | PostgreSQL or SQLite | A broker (Redis, RabbitMQ, ...) | Redis | Your Django database |
| Works without Django | Yes | Yes | Yes | No |
| Enqueue in your DB transaction | Yes (Django mode) | No | No | Yes |
| Cron scheduling | Built in | Celery beat | Depends on the version; see the RQ docs | Not built in |
| Chains, groups, chords | Yes | Yes | Dependencies only | No |
| Dashboard | Built in | Flower (separate) | Separate package | Django admin |
| Maturity | Beta | Very mature, large ecosystem | Mature | Young |

Where the others are better:

- **Celery** is far more mature and has exchanges, routing keys, remote control, autoscaling and pluggable result backends. sqlery has none of these. [docs/CELERY_PARITY.md](docs/CELERY_PARITY.md) lists what is and is not covered.
- **RQ** is simpler if you already run Redis.
- **django-tasks** follows Django's official Tasks API. If you only need basic background tasks in Django, it is the smaller choice. sqlery can also run with `EXECUTION_MODE = "django-tasks"`.

Throughput: there is no benchmark against the other tools. The project's own measurement on the 0.28 code is about 28 no-op jobs per second per worker on a local PostgreSQL, and about 64 on SQLite ([CHANGELOG](CHANGELOG.md)). A broker-based queue is the usual choice above that range. Scale sqlery with more workers first.

Migration guides: [from Celery](docs/MIGRATION_FROM_CELERY.md), [from RQ](docs/MIGRATION_FROM_RQ.md), [from django-tasks-scheduler](docs/MIGRATION_FROM_DJANGO_TASKS_SCHEDULER.md). The drop-in shims are `sqlery.compat.rq` and `sqlery.compat.scheduler`.

## Agents / LLM

If you are a coding agent, or you use one, read [llms.txt](llms.txt) first. It is the short, verified API summary: install, job API, worker commands, run modes, gotchas, and the list of stale docs. It marks each item it did not run as UNVERIFIED. Its install lines use the same pinned GitHub commands as [Install](#install).

Rules for generated code:

- Call `sqlery.compat.initialize()` in standalone mode. `sqlery.backends.BackendFactory` was removed in v0.13.
- Import `initialize` from `sqlery.compat`, not from `sqlery`.
- If a doc and the source disagree, the source is correct. Start at [src/sqlery/\_\_init\_\_.py](src/sqlery/__init__.py).

## Documentation

- [llms.txt](llms.txt): the verified API summary.
- [CHANGELOG.md](CHANGELOG.md): current behavior and upgrade notes.
- [docs/CELERY_PARITY.md](docs/CELERY_PARITY.md): results, retries, canvas, revoke, signals, time limits, solar schedules.
- [docs/CONFIGURATION.md](docs/CONFIGURATION.md) and [docs/ENVIRONMENT_VARIABLES.md](docs/ENVIRONMENT_VARIABLES.md): settings and `SQLERY_*` variables.
- [docs/SECURITY.md](docs/SECURITY.md): dashboard authentication, webhook SSRF protection, the `ALLOWED_TASK_MODULES` allowlist.
- [docs/design/queue-robustness.md](docs/design/queue-robustness.md): claiming, the ready queue, partitions, recovery.
- [docs/guides/scheduled-tasks-vs-tick-loop.md](docs/guides/scheduled-tasks-vs-tick-loop.md): when to use a scheduled task.
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md): common problems.
- Examples: [job dependencies](examples/job_dependencies_usage.md), [rate limiting](examples/rate_limiting_usage.md), [tag concurrency](examples/tag_concurrency_usage.md), [webhooks](examples/webhooks_usage.md).

Some older docs are behind the code. [llms.txt](llms.txt) lists the known stale pages.

## Contributing

```bash
git clone https://github.com/intrepid-g/sqlery.git
cd sqlery
uv pip install -e ".[dev]"
uv run pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md). Report bugs in the [issue tracker](https://github.com/intrepid-g/sqlery/issues). Report security problems as described in [SECURITY.md](SECURITY.md).

## License

MIT. See [LICENSE](LICENSE).
