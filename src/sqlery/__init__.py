"""Sqlery - Job queue + cron scheduling for Python.

A lightweight, database-backed job queue with support for PostgreSQL and SQLite.

This is a monorepo containing:
- Core standalone package (future: pip install sqlery)
- Django integration (future: pip install django-sqlery)

For Django projects:
    # In settings.py INSTALLED_APPS
    'sqlery.django_sqlery',

    # In your tasks
    from sqlery.django_sqlery.decorators import job
    from sqlery.django_sqlery.models import QueuedJob

For standalone projects (FastAPI, Flask, etc.):
    from sqlery.compat import initialize, get_backend
    from sqlery.core import Queue, Worker
"""

__version__ = "0.22.0"

# Re-exports for backward compatibility
from .core.job_queue import (
    Queue,
    enqueue,
    enqueue_at,
    get_queue,
    claim_job,
    get_queue_stats,
    cancel_job,
    retry_failed_jobs,
)
from .core.worker import JobExecutor, WorkerProcess

# Alias WorkerProcess as Worker for backward compatibility
Worker = WorkerProcess

# Try to import Django-specific decorators (may fail in non-Django mode)
try:
    from .django_sqlery.decorators import job, async_job
except ImportError:
    # Not in Django mode - decorators not available
    pass

# Try to import AsyncQueue (may not be available in all modes)
try:
    # from .async_worker import AsyncQueue  # Wrong module — AsyncQueue lives in async_queue.py
    from .async_queue import AsyncQueue
except ImportError:
    pass

# django-tasks-scheduler compatibility layer available as:
#   from sqlery.compat.scheduler import Task, TaskType, ...
# Not imported here to avoid circular imports during Django app loading.

# RQ compatibility layer available as:
#   from sqlery.compat.rq import Queue, get_queue, Retry, get_current_job, ...
# Not imported here to avoid circular imports during Django app loading.

__all__ = [
    "__version__",
    "Queue",
    "Worker",
    "WorkerProcess",
    "JobExecutor",
    "enqueue",
    "enqueue_at",
    "get_queue",
    "claim_job",
    "get_queue_stats",
    "cancel_job",
    "retry_failed_jobs",
]
