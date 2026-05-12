# Coding Conventions

**Analysis Date:** 2026-05-12

## Naming Patterns

**Files:**
- Use `snake_case.py` for all Python modules
- Use descriptive names that reflect module purpose: `worker_claiming.py`, `daemon_runner.py`, `rate_limit_utils.py`
- Django management commands use snake_case: `run_jobs.py`, `cleanup_jobs.py`, `run_scheduled_tasks.py`
- Test files follow `test_<subject>.py` pattern: `test_models.py`, `test_queue.py`, `test_atomic_claiming.py`
- Migration files use numbered prefix: `0001_initial.py`, `0002_worker_multi_worker.py`
- Alembic migrations use date prefix: `20250101_0001_initial_schema.py`

**Functions:**
- Use `snake_case` for all functions and methods
- Prefix private methods with single underscore: `_cleanup_stale_jobs()`, `_retry_job()`, `_spawn_next_worker()`
- Use verb-noun naming: `mark_running()`, `get_due_tasks()`, `execute_job()`, `calculate_retry_delay()`
- Boolean-returning methods use `is_`, `has_`, `can_`, `should_` prefixes: `is_alive()`, `can_execute_job()`, `should_retry()`

**Variables:**
- Use `snake_case` for all variables and parameters
- Use `UPPER_SNAKE_CASE` for module-level constants: `STATUS_CHOICES`, `DEFAULTS`, `SCHEDULE_TYPE_CHOICES`
- Use descriptive names for query results: `queued_jobs`, `due_task_ids`, `processed_jobs`

**Types/Classes:**
- Use `PascalCase` for classes: `TaskExecutor`, `QueuedJob`, `ScheduledTask`, `JobFunction`, `AsyncJobFunction`
- Use `PascalCase` for custom exceptions: `ConcurrentModificationError`
- Use `PascalCase` for Django model managers: `ScheduledTaskManager`
- Backward compatibility aliases use PascalCase: `TaskExecution = QueuedJob`, `Worker = WorkerProcess`

**Constants:**
- Django model choice lists are module-level tuples of tuples:
  ```python
  STATUS_CHOICES = [
      ("queued", "Queued"),
      ("running", "Running"),
      ("success", "Success"),
      ("failed", "Failed"),
      ("archived", "Archived"),
  ]
  ```

## Code Style

**Formatting:**
- Tool: `black` (configured in `pyproject.toml`)
- Line length: 100 characters
- Target Python version: 3.10+

**Linting:**
- Tool: `ruff` (configured in `pyproject.toml`)
- Line length: 100 characters
- Target Python version: py310

**Type Hints:**
- Use modern union syntax: `str | None` (not `Optional[str]`)
- Use `Callable`, `Any` from `typing`
- Use return type annotations on public methods:
  ```python
  def should_retry(self) -> bool:
  def get_status(self) -> str:
  def get_by_name(cls, job_name: str) -> "QueuedJob | None":
  ```

## Import Organization

**Order:**
1. Standard library imports (`import os`, `import logging`, `import traceback`)
2. Third-party imports (`from django.db import models`, `from croniter import croniter`)
3. Local/project imports (`from .models import QueuedJob`, `from sqlery.core.utils import ...`)

**Patterns:**
- Use relative imports within a package: `from .models import QueuedJob, ScheduledTask`
- Use absolute imports for cross-package references: `from sqlery.core.utils import calculate_next_run`
- Use lazy imports inside functions for optional dependencies or circular import prevention:
  ```python
  def mark_success(self, output=""):
      from django.db.models import F
      # ...
  ```
- Guard optional dependency imports with try/except:
  ```python
  try:
      from sqlery.async_queue import AsyncQueue
  except ImportError:
      AsyncQueue = None
  ```

**Path Aliases:**
- No path aliases configured; all imports use the standard Python module path
- Source code lives in `src/sqlery/` and is installed as the `sqlery` package via hatchling

## Error Handling

**Patterns:**
- Use custom exceptions for domain-specific errors: `ConcurrentModificationError` in `src/sqlery/django_sqlery/models.py`
- Use optimistic locking with version field for concurrent modification detection:
  ```python
  rows_updated = QueuedJob.objects.filter(id=self.id, version=expected_version).update(
      status="running",
      version=F("version") + 1,
  )
  if rows_updated == 0:
      raise ConcurrentModificationError(
          f"Job {self.id} was modified by another process (version conflict)"
      )
  ```
- Wrap external operations in try/except with logging, never crash silently:
  ```python
  except Exception as e:
      logger.error(f"Failed to sync EventBridge rule for task '{self.name}': {e}")
  ```
- API views return structured JSON errors with HTTP status codes:
  ```python
  return JsonResponse({'error': 'Task not found'}, status=404)
  return JsonResponse({'error': f'Invalid action: {action}'}, status=400)
  ```
- Task execution captures full tracebacks:
  ```python
  error_traceback = tb.format_exc()
  job.mark_failed(error=human_error, traceback=error_traceback, termination_reason=termination_reason)
  ```

**Error Propagation Strategy:**
- Background/async operations: catch, log, and continue (never crash the worker loop)
- Synchronous API calls: propagate to caller with structured error responses
- Signal handlers (SIGALRM for timeout): raise `TimeoutError` to be caught by job executor
- Database operations: use atomic transactions with rollback on failure

## Logging

**Framework:** Python standard `logging` module

**Patterns:**
- Initialize logger at module level:
  ```python
  logger = logging.getLogger(__name__)
  ```
- Use appropriate log levels:
  - `logger.info()` for normal operations: job completion, worker startup
  - `logger.warning()` for recoverable issues: stale job cleanup, missing dependencies
  - `logger.error()` for failures: failed kills, database errors
  - `logger.debug()` for verbose details: spawned subprocess commands
  - `logger.exception()` for errors with traceback: callback failures
- Use f-strings in log messages:
  ```python
  logger.info(f"Enqueued job for scheduled task '{task.name}' in queue '{task.queue_name}'")
  logger.warning(f"Cleaned up stale job {job.id} (running {int(running_duration)}s > {threshold_seconds}s threshold)")
  ```

## Comments

**When to Comment:**
- Module-level docstrings explain purpose: `"""Task execution engine for sqlery."""`
- Class docstrings describe responsibility and schema synchronization:
  ```python
  class QueuedJob(models.Model):
      """A job in the queue, waiting to be executed or already processed.

      Schema synchronized with core.models.QueuedJob (SQLModel).
      """
  ```
- Use `# #CLEANUP:` prefix for migration/backward-compatibility stubs that should be removed later:
  ```python
  # #CLEANUP: This file has been moved to src/sqlery/django_sqlery/
  # This stub exists for backward compatibility during migration.
  ```
- Commented-out code blocks are preserved with `# Old:` or `# #` prefix to document what was replaced
- Inline comments for non-obvious logic:
  ```python
  # Exponential backoff: retry_backoff * (2 ^ retry_count)
  return self.retry_backoff * (2**self.retry_count)
  ```

**Docstrings:**
- Use Google-style docstrings with Args, Returns, Raises, Example sections:
  ```python
  def execute_job(self, job):
      """Execute a single queued job.

      Args:
          job: QueuedJob instance

      Returns:
          QueuedJob: The updated job instance
      """
  ```
- All public methods and classes have docstrings
- Django model fields use `help_text` parameter for documentation:
  ```python
  task_path = models.CharField(
      max_length=500,
      help_text="Python path to callable (e.g., 'myapp.tasks.my_function')",
  )
  ```

## Function Design

**Size:** Functions are generally 20-60 lines. Larger methods like `run_queue_workers` (~60 lines) and `_cleanup_stale_jobs` (~70 lines) are the upper bound. Break complex logic into private helper methods.

**Parameters:**
- Use keyword arguments with defaults for optional params:
  ```python
  def run_queue_workers(self, queue_name=None, once=False, max_jobs=None):
  ```
- Use `**kwargs` for pass-through arguments in decorators and fluent APIs
- Override pattern: parameter > decorator default > system config default:
  ```python
  if queue is not None:
      effective_queue = queue
  elif self.queue_name is not None:
      effective_queue = self.queue_name
  else:
      effective_queue = get_config("DEFAULT_QUEUE", "default")
  ```

**Return Values:**
- Return the mutated object for method chaining: `execute_job()` returns the `QueuedJob`
- Return `None` for "not found" or "skipped" cases: `_enqueue_for_scheduled_task()` returns `None` if already queued
- Return lists for batch operations: `run_queue_workers()` returns `list[QueuedJob]`
- Return tuples for multi-value results: `check_dependencies_met()` returns `(bool, list)`
- Return `bool` for success/failure: `atomic_claim_job()` returns `True`/`False`

## Module Design

**Exports:**
- Use `__all__` in `__init__.py` to define public API:
  ```python
  __all__ = [
      "__version__",
      "Queue", "Worker", "WorkerProcess", "JobExecutor",
      "enqueue", "enqueue_at", "get_queue", "claim_job",
      "get_queue_stats", "cancel_job", "retry_failed_jobs",
  ]
  ```
- Re-export backward compatibility aliases at package level

**Backward Compatibility Stubs:**
- When code is moved, keep a stub file that re-exports from the new location:
  ```python
  # src/sqlery/models.py (stub)
  try:
      from sqlery.django_sqlery.models import QueuedJob, ScheduledTask, Worker
      TaskExecution = QueuedJob  # Backward compatibility alias
  except ImportError:
      pass
  ```
- This pattern is used in: `src/sqlery/models.py`, `src/sqlery/executor.py`, `src/sqlery/decorators.py`, `src/sqlery/utils.py`

**Barrel Files:**
- `src/sqlery/__init__.py` serves as the main barrel file, re-exporting core functionality
- Django integration exports are separate in `src/sqlery/django_sqlery/__init__.py`

## Django-Specific Conventions

**Model Meta:**
- Always set explicit `db_table` name: `db_table = "sqlery_queued_job"`
- Define `ordering` in Meta for consistent query results
- Add `verbose_name` and `verbose_name_plural` for admin display
- Add database indexes for commonly queried fields:
  ```python
  indexes = [
      models.Index(fields=["queue_name", "status", "-priority", "created_at"]),
  ]
  ```

**Settings:**
- Use a single `DJANGO_SQL_JOBS` dict in Django settings
- All settings have defaults defined in `src/sqlery/django_sqlery/settings.py`
- Access settings via `get_setting()` which provides self-healing fallback:
  ```python
  trigger_mode = get_setting("TRIGGER_MODE", "middleware")
  ```

**Management Commands:**
- Located in `src/sqlery/django_sqlery/management/commands/`
- Use Django's `BaseCommand` class

---

*Convention analysis: 2026-05-12*
