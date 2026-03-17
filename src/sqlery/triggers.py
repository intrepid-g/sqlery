"""Trigger mechanisms for sqlery."""

import logging

from .subprocess_executor import get_execution_strategy, run_scheduler_subprocess, run_worker_subprocess

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


def _enqueue_django_tasks():
    """Enqueue jobs for due tasks via django-tasks."""
    try:
        from django_tasks import task

        @task()
        def enqueue_due_tasks():
            from .executor import TaskExecutor

            executor = TaskExecutor()
            return executor.run_due_tasks()

        enqueue_due_tasks()
        logger.info("Triggered scheduler via django-tasks")

    except ImportError:
        logger.warning("django-tasks not available, falling back to synchronous")
        _enqueue_synchronously()


def _process_queue_django_tasks(queue_name: str | None = None):
    """Process queue via django-tasks."""
    try:
        from django_tasks import task

        @task()
        def process_queue():
            from .executor import TaskExecutor

            executor = TaskExecutor()
            return executor.run_queue_workers(queue_name=queue_name, once=True)

        process_queue()
        logger.info("Triggered worker via django-tasks")

    except ImportError:
        logger.warning("django-tasks not available, falling back to synchronous")
        _process_queue_synchronously(queue_name)


# ===== Synchronous/Thread Mode =====


def _enqueue_synchronously():
    """Enqueue jobs for due tasks synchronously (blocking)."""
    from .executor import TaskExecutor

    executor = TaskExecutor()
    executor.run_due_tasks()
    logger.info("Enqueued jobs for due tasks synchronously")


def _process_queue_synchronously(queue_name: str | None = None):
    """Process queue synchronously (blocking)."""
    from .executor import TaskExecutor

    executor = TaskExecutor()
    executor.run_queue_workers(queue_name=queue_name)
    logger.info("Processed queue synchronously")
