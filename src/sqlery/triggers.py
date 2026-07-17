"""Trigger mechanisms for sqlery."""

import logging

from sqlery.core.worker import TaskExecutor
from .subprocess_executor import get_execution_strategy, run_scheduler_subprocess, run_worker_subprocess

try:
    from django_tasks import task
except ImportError:
    task = None

logger = logging.getLogger(__name__)


def trigger_due_tasks():
    """Check for due scheduled tasks and enqueue jobs.

    Execution mode determined by EXECUTION_MODE setting:
    - 'subprocess': Run in isolated subprocess (prevents memory leaks)
    - 'django-tasks': Use django-tasks for async execution
    - 'thread': Run synchronously in current thread
    - 'auto': Prefer django-tasks if available, otherwise subprocess
    """
    strategy = get_execution_strategy()

    if strategy == "subprocess":
        _enqueue_subprocess()
    elif strategy == "django-tasks":
        _enqueue_django_tasks()
    else:  # thread
        _enqueue_synchronously()


def trigger_queue_workers(queue_name: str | None = None):
    """Trigger queue workers to process jobs.

    Execution mode determined by EXECUTION_MODE setting:
    - 'subprocess': Run in isolated subprocess (prevents memory leaks)
    - 'django-tasks': Use django-tasks for async execution
    - 'thread': Run synchronously in current thread
    - 'auto': Prefer django-tasks if available, otherwise subprocess
    """
    strategy = get_execution_strategy()

    if strategy == "subprocess":
        _process_queue_subprocess(queue_name)
    elif strategy == "django-tasks":
        _process_queue_django_tasks(queue_name)
    else:  # thread
        _process_queue_synchronously(queue_name)


# ===== Subprocess Mode =====


def _enqueue_subprocess():
    """Enqueue jobs for due tasks in subprocess (memory safe)."""
    logger.info("Triggering scheduler via subprocess")
    try:
        result = run_scheduler_subprocess()
        if result["returncode"] != 0:
            logger.error(f"Scheduler subprocess failed: {result.get('stderr', 'Unknown error')}")
    except Exception as e:
        logger.error(f"Scheduler subprocess error: {e}")


def _process_queue_subprocess(queue_name: str | None = None):
    """Process queue in subprocess (memory safe)."""
    logger.info(f"Triggering worker via subprocess (queue={queue_name or 'all'})")
    try:
        result = run_worker_subprocess(queue_name)
        if result["returncode"] != 0:
            logger.error(f"Worker subprocess failed: {result.get('stderr', 'Unknown error')}")
    except Exception as e:
        logger.error(f"Worker subprocess error: {e}")


# ===== Django-Tasks Mode =====


def _run_due_tasks_job():
    """Module-level job body for django-tasks scheduler enqueue.

    django-tasks requires the decorated function to be a module-level function
    (it rejects closures via ``is_module_level_function``), so this cannot be
    nested inside ``_enqueue_django_tasks``.

    Returns processed job ids, not model instances: django-tasks stores the
    return value via ``normalize_json``, which raises TypeError on QueuedJob
    objects and would mark every productive run FAILED.
    """
    executor = TaskExecutor()
    # tried returning executor.run_due_tasks() directly → list[QueuedJob] is not
    # JSON-serializable; django-tasks recorded FAILED whenever jobs were processed
    jobs = executor.run_due_tasks()
    return [str(job.id) for job in jobs]


def _run_queue_job(queue_name: str | None = None):
    """Module-level job body for django-tasks queue processing.

    See ``_run_due_tasks_job`` docstring for why this must be module-level
    and why it returns job ids instead of QueuedJob instances.
    """
    executor = TaskExecutor()
    jobs = executor.run_queue_workers(queue_name=queue_name, once=True)
    return [str(job.id) for job in jobs]


# Built once at import: django-tasks Tasks are frozen declarations and
# get_backend().validate_task() runs at construction, so per-call task(...)
# re-validated on every trigger. Module-level also makes them importable
# for django-tasks' db_worker introspection.
if task is not None:
    _due_tasks_task = task(_run_due_tasks_job)
    _queue_task = task(_run_queue_job)
else:
    _due_tasks_task = None
    _queue_task = None


def _enqueue_django_tasks():
    """Enqueue jobs for due tasks via django-tasks.

    Note: without a configured Django ``TASKS`` setting, django-tasks falls
    back to ImmediateBackend, which executes synchronously in the calling
    thread — configure a real backend (e.g. database) for async execution.
    """
    if _due_tasks_task is None:
        logger.warning("django-tasks not available, falling back to synchronous")
        _enqueue_synchronously()
        return

    # Old: task(_run_due_tasks_job).enqueue() per call — rebuilt/validated the Task each trigger
    _due_tasks_task.enqueue()
    logger.info("Triggered scheduler via django-tasks")


def _process_queue_django_tasks(queue_name: str | None = None):
    """Process queue via django-tasks.

    See ``_enqueue_django_tasks`` for the ImmediateBackend caveat.
    """
    if _queue_task is None:
        logger.warning("django-tasks not available, falling back to synchronous")
        _process_queue_synchronously(queue_name)
        return

    _queue_task.enqueue(queue_name)
    logger.info("Triggered worker via django-tasks")


# ===== Synchronous/Thread Mode =====


def _enqueue_synchronously():
    """Enqueue jobs for due tasks synchronously (blocking)."""
    # from .executor import TaskExecutor  # moved to top-level

    executor = TaskExecutor()
    executor.run_due_tasks()
    logger.info("Enqueued jobs for due tasks synchronously")


def _process_queue_synchronously(queue_name: str | None = None):
    """Process queue synchronously (blocking)."""
    # from .executor import TaskExecutor  # moved to top-level

    executor = TaskExecutor()
    executor.run_queue_workers(queue_name=queue_name)
    logger.info("Processed queue synchronously")
