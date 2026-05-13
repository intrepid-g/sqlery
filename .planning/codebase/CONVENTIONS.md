# Coding Conventions

**Analysis Date:** 2026-05-13

## Naming Patterns

**Files (modules):**
- `snake_case.py` for all Python modules
- Descriptive, purpose-reflecting names: `worker_claiming.py`, `daemon_manager.py`, `rate_limit_utils.py`, `db_resilience.py`, `subprocess_executor.py`
- Django management commands use `snake_case`: `src/sqlery/django_sqlery/management/commands/run_jobs.py`, `cleanup_jobs.py`, `run_scheduled_tasks.py`
- Tests follow `test_<subject>.py`: `tests/test_models.py`, `tests/test_queue.py`, `tests/test_atomic_claiming.py`
- Django migrations: numbered prefix `0001_initial.py`, `0002_worker_multi_worker.py` (24 migrations under `src/sqlery/django_sqlery/migrations/`)
- Alembic migrations: date prefix `20250101_0001_initial_schema.py` under `alembic/versions/`

**Functions and methods:**
- `snake_case` everywhere
- Private/internal: single leading underscore — `_cleanup_stale_jobs()`, `_retry_job()`, `_spawn_next_worker()`, `_import_task()`
- Verb-noun naming: `mark_running()`, `get_due_tasks()`, `execute_job()`, `calculate_retry_delay()`, `atomic_claim_job()`
- Boolean predicates use `is_`, `has_`, `can_`, `should_` prefixes: `is_alive()`, `can_execute_job()`, `should_retry()`

**Variables and parameters:**
- `snake_case` for locals and parameters
- `UPPER_SNAKE_CASE` for module-level constants: `STATUS_CHOICES`, `DEFAULTS` (in `src/sqlery/django_sqlery/settings.py`), `SCHEDULE_TYPE_CHOICES`
- Descriptive collection names: `queued_jobs`, `due_task_ids`, `processed_jobs`

**Classes:**
- `PascalCase`: `TaskExecutor`, `QueuedJob`, `ScheduledTask`, `JobFunction`, `AsyncJobFunction`, `WorkerProcess`, `DaemonManager`, `StandaloneConfig`
- Custom exceptions: `PascalCase` ending in `Error` — `ConcurrentModificationError` (`src/sqlery/django_sqlery/models.py:30`)
- Django model managers: `PascalCase` ending in `Manager` — `ScheduledTaskManager`
- Backward-compat aliases re-exported in PascalCase: `Worker = WorkerProcess` (`src/sqlery/__init__.py:38`), `TaskExecution = QueuedJob`

**Django model choices:**
- Module-level tuples of tuples (e.g., `STATUS_CHOICES` in `src/sqlery/core/models.py`)

## Code Style

**Formatting:**
- Tool: `black` (configured in `pyproject.toml` `[tool.black]`)
- Line length: **100**
- Target: `py310`
- Run: `uv run black src/ tests/`

**Linting:**
- Tool: `ruff` (configured in `pyproject.toml` `[tool.ruff]`)
- Line length: **100**, target `py310`
- Run: `uv run ruff check src/ tests/`

**Type hints:**
- Use modern union syntax: `str | None` (not `Optional[str]`) — required since minimum is Python 3.10
- Import from `typing` only what's not built-in: `Callable`, `Any`
- Public methods/functions carry return type annotations:
  ```python
  def execute_job(self, job: QueuedJob) -> QueuedJob: ...
  def atomic_claim_job(self, job_id: int, worker_id: str) -> bool: ...
  ```
- Lowercase generics where possible: `list[int]`, `dict[str, Any]`, `tuple[bool, list]`

## Import Organization

**Within a package — relative imports:**
```python
from .models import QueuedJob, ScheduledTask
from ..compat import get_backend, get_config
from .utils import import_task
```

**Cross-package — absolute imports:**
```python
from sqlery.core.utils import calculate_next_run
from sqlery.core.db_resilience import configure_connection_resilience
```
Required inside `src/sqlery/compat/__init__.py` because relative imports there would resolve in the wrong package.

**Optional / circular-import-prone modules — lazy imports inside functions:**
```python
def get_backend():
    from sqlery.django_sqlery.backend import DjangoBackend
    ...
```

**Optional dependencies — guarded try/except:**
```python
try:
    from django.db import connections, close_old_connections
except ImportError:
    connections = None
    close_old_connections = None
```
See `src/sqlery/core/worker.py:19-23`, `src/sqlery/__init__.py:41-52`.

**No path aliases.** Source lives at `src/sqlery/` and is installed as the `sqlery` package via hatchling (`pyproject.toml` `[tool.hatch.build.targets.wheel]`).

## Error Handling

**Custom exceptions for domain errors:**
- `ConcurrentModificationError` raised on optimistic-lock CAS failures in `src/sqlery/django_sqlery/models.py:30,630,666,719`.

**Optimistic locking pattern (SQLite path):**
```python
updated = QueuedJob.objects.filter(id=self.id, version=self.version).update(
    status='running', version=self.version + 1, ...
)
if updated == 0:
    raise ConcurrentModificationError(...)
```

**Try/except policy:**
- **Background loops** (worker main loop, daemon cycle, scheduler tick): catch broadly, log with traceback, sleep, continue. Never let a single error kill the loop.
- **Synchronous API calls / web handlers**: propagate to caller; FastAPI/Django views return structured JSON errors with HTTP status codes.
- **Signal handlers** (SIGALRM): raise `TimeoutError` to be caught by the job executor. No DB calls inside signal handlers (would corrupt psycopg connections).
- **DB-level transient errors**: wrap with `retry_on_db_error` decorator (`src/sqlery/core/db_resilience.py`) for exponential-backoff retries on deadlocks, connection drops, "database is locked".
- **Job execution**: capture `traceback.format_exc()` into `QueuedJob.error_traceback`; retry with exponential backoff if `max_retries > 0`.

**Atomic DB ops:** use `transaction.atomic()` (Django) / SQLAlchemy session context with rollback on failure. Postgres claim path uses `SELECT FOR UPDATE SKIP LOCKED`; SQLite uses CAS on `version`.

## Logging

**Module-level logger:**
```python
import logging
logger = logging.getLogger(__name__)
```
Convention in every core module — see `src/sqlery/core/claiming.py:18`, `worker.py:27`, `daemon.py:36`, `scheduler.py:9`, `registry.py:8`, `cleanup.py:8`, `db_resilience.py:20`.

**Levels:**
- `logger.debug(...)` — verbose tracing
- `logger.info(...)` — normal lifecycle events (`"Executing job {job.id}: {job.task_path}"`)
- `logger.warning(...)` — unexpected but recoverable (`"Job {job.id} has status '{status}', skipping"`)
- `logger.error(...)` / `logger.exception(...)` — failures; `exception()` inside `except` blocks to include traceback

**Use f-strings in log messages.** Always include the relevant ID (`job.id`, `worker_id`, `task_id`) so logs are greppable across processes.

**Central config:** `src/sqlery/core/log_config.py` configures handlers/levels for CLI entry points.

## Comments and Docstrings

**Module docstrings** explain purpose in one line:
```python
"""Django-agnostic worker execution logic with fork-per-job support."""
```

**Class docstrings** describe responsibility and any schema invariants:
```python
class JobExecutor:
    """Executes jobs with retry logic, timeout support, and crash recovery.

    Works in both Django and standalone modes via backend abstraction.
    """
```

**Public methods/classes**: Google-style docstrings with `Args`, `Returns`, `Raises`, `Example` where useful.

**Django model fields**: use `help_text=` for inline documentation (it doubles as admin tooltip).

**Dead code / migration stubs:**
- Prefix with `# #CLEANUP:` for code kept for backward compatibility that should be removed later.
- Commented-out replaced code uses `# Old:` or `# #` prefix to record what was swapped (see `src/sqlery/__init__.py:49`).
- **Project rule (from memory):** comment and date-mark dead code rather than deleting it outright.

**Inline comments**: only for non-obvious logic (fork safety, signal-handler limitations, CAS races, SQLite-vs-Postgres branches).

## Function Design

- **Keyword arguments with defaults** for optional params. Keep positional args to the few that are always required.
- **`**kwargs` pass-through** in decorators and fluent APIs (`@job`, `Queue.enqueue`, `.delay`).
- **Override precedence**: explicit parameter > decorator default > system config default (via `get_setting()` / `get_config()`).

**Return-value conventions:**
- Mutated domain object for chaining: `execute_job()` returns the `QueuedJob` it updated.
- `None` for "not found" or "skipped" (e.g., `_enqueue_for_scheduled_task()` when already queued).
- `list[...]` for batch operations: `run_queue_workers() -> list[QueuedJob]`.
- `tuple` for multi-value results: `check_dependencies_met() -> tuple[bool, list]`.
- `bool` for success/failure of atomic ops: `atomic_claim_job() -> bool`.

## Module Design

**Public API via `__all__`** in `__init__.py`:
- `src/sqlery/__init__.py` declares `__all__` listing every public symbol (`Queue`, `Worker`, `WorkerProcess`, `JobExecutor`, `enqueue`, `enqueue_at`, `get_queue`, `claim_job`, `get_queue_stats`, `cancel_job`, `retry_failed_jobs`, `__version__`).
- Django-facing exports live separately in `src/sqlery/django_sqlery/__init__.py`.

**Backward-compatibility stubs:** when a module moves, keep the old path as a thin re-export shim. Active examples:
- `src/sqlery/models.py` → re-exports from `sqlery.django_sqlery.models`
- `src/sqlery/executor.py`, `src/sqlery/decorators.py`, `src/sqlery/utils.py` — same pattern

**Aliases for renames:** keep both names exported (`Worker = WorkerProcess`, `TaskExecution = QueuedJob`).

**Barrel files:**
- `src/sqlery/__init__.py` — main barrel for the public API (conditionally imports Django decorators in a `try/except ImportError`).
- `src/sqlery/core/__init__.py`, `src/sqlery/compat/__init__.py` — internal barrels.

**Avoid module-level Django imports in core code.** Core (`src/sqlery/core/`) must work without Django installed; route DB access through the `DatabaseBackend` ABC defined in `src/sqlery/compat/__init__.py`.

## Django-Specific Conventions

**Models** (`src/sqlery/django_sqlery/models.py`):
- Always set an explicit `db_table`: `db_table = "sqlery_queued_job"` (prevents app-label collisions).
- Define `ordering` in `Meta` for deterministic queries.
- Set `verbose_name` and `verbose_name_plural` for admin readability.
- Add indexes for commonly queried fields (`status`, `queue_name`, `next_run_at`, `version`).
- Use a `version` field + CAS update for optimistic locking (SQLite path).

**Settings:**
- Single dict `DJANGO_SQL_JOBS` in `settings.py`.
- Defaults centralized in `src/sqlery/django_sqlery/settings.py` (`DEFAULTS` dict).
- Always read via `get_setting(name, default)` — provides self-healing fallback if a key was added in a newer release than the user's `settings.py` knows about.
- Migration helper `migrate_settings()` translates legacy RQ / django-tasks-scheduler config dicts.

**Management commands** (`src/sqlery/django_sqlery/management/commands/`):
- Subclass `django.core.management.base.BaseCommand`.
- Mirror the Typer CLI commands (`daemon`, `run_jobs`, `workers`, `cleanup_jobs`, `run_scheduled_tasks`) so Django users get a native interface.

**App config** (`src/sqlery/django_sqlery/apps.py`):
- Wires the `connection_created` signal to enable SQLite WAL mode + `busy_timeout`.
- Registers admin / dashboard URLs.

**Fork safety:** call `django.db.connections.close_all()` before `os.fork()`; reopen in both parent and child. Encapsulated in `_reset_db_connections()` in worker code.

---

*Convention analysis: 2026-05-13*
