"""Compatibility layer for django-tasks-scheduler (v2.1.1).

Drop-in replacement: change only imports to migrate from django-tasks-scheduler to SQLery.

    # Before (django-tasks-scheduler)
    from scheduler.models import Task, TaskType, TaskArg, TaskKwarg
    from scheduler.models import get_scheduled_task, run_task
    from scheduler import job
    from scheduler.helpers.queues import Queue, get_queue, get_all_workers
    from scheduler.redis_models import JobModel, JobStatus

    # After (SQLery)
    from sqlery.compat.scheduler import Task, TaskType, TaskArg, TaskKwarg
    from sqlery.compat.scheduler import get_scheduled_task, run_task
    from sqlery.compat.scheduler import job
    from sqlery.compat.scheduler import Queue, get_queue, get_all_workers
    from sqlery.compat.scheduler import JobModel, JobStatus
"""

import logging
import warnings
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from typing import Any, Callable

warnings.warn(
    "sqlery.compat.scheduler is deprecated and will be removed in v3.2.0. "
    "Use sqlery.django_sqlery.queue.Queue, sqlery.django_sqlery.models, "
    "and sqlery.django_sqlery.decorators directly.",
    DeprecationWarning,
    stacklevel=2,
)

from sqlery.django_sqlery.decorators import job
from sqlery.django_sqlery.models import QueuedJob, ScheduledTask as _ScheduledTaskModel, Worker
from sqlery.django_sqlery.queue import Queue as _SQLeryQueue
from sqlery.django_sqlery.backend import DjangoBackend as _DjangoBackend
# from sqlery.django_sqlery.utils import (  # Promoted to core
#     calculate_next_run,
#     enqueue_task,
#     import_task,
# )
from sqlery.core.utils import calculate_next_run, import_task
from sqlery.django_sqlery.utils import enqueue_task  # Django-specific, stays

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# JobStatus — mirrors django-tasks-scheduler's JobStatus enum
# ---------------------------------------------------------------------------

class JobStatus(str, Enum):
    """Job lifecycle states, matching django-tasks-scheduler naming."""

    SCHEDULED = "scheduled"
    QUEUED = "queued"
    CANCELED = "cancelled"
    STARTED = "running"
    FINISHED = "success"
    FAILED = "failed"
    STOPPED = "failed"  # sqlery has no stopped state; map to failed


# ---------------------------------------------------------------------------
# JobModel — thin wrapper around QueuedJob for d-t-s API compatibility
# ---------------------------------------------------------------------------

class JobModel:
    """Wraps a QueuedJob to expose the django-tasks-scheduler JobModel API."""

    def __init__(self, queued_job: QueuedJob):
        self._job = queued_job

    # --- Identity ---

    @property
    def name(self) -> str:
        """Job identifier: job_name if set, otherwise string form of PK."""
        return self._job.job_name or str(self._job.pk)

    @property
    def id(self) -> int:
        return self._job.pk

    # --- Status ---

    @property
    def status(self) -> JobStatus:
        _map = {
            "queued": JobStatus.QUEUED,
            "running": JobStatus.STARTED,
            "success": JobStatus.FINISHED,
            "failed": JobStatus.FAILED,
            "cancelled": JobStatus.CANCELED,
        }
        return _map.get(self._job.status, JobStatus.QUEUED)

    @property
    def is_queued(self) -> bool:
        return self._job.status == "queued"

    @property
    def is_canceled(self) -> bool:
        return self._job.status == "cancelled"

    @property
    def is_failed(self) -> bool:
        return self._job.status == "failed"

    # --- Callable ---

    @property
    def func_name(self) -> str:
        return self._job.task_path

    @property
    def func(self) -> Callable:
        return import_task(self._job.task_path)

    # --- Timestamps ---

    @property
    def created_at(self):
        return self._job.created_at

    @property
    def enqueued_at(self):
        return getattr(self._job, "scheduled_at", None) or self._job.created_at

    @property
    def started_at(self):
        return getattr(self._job, "started_at", None)

    @property
    def ended_at(self):
        return getattr(self._job, "finished_at", None)

    # --- Result ---

    @property
    def return_value(self):
        return getattr(self._job, "output", None)

    @property
    def exc_string(self) -> str | None:
        return getattr(self._job, "traceback", None)

    # --- Queue ---

    @property
    def queue_name(self) -> str:
        return self._job.queue_name

    # --- meta: DB-backed via QueuedJob.meta ---

    @property
    def meta(self) -> dict:
        """Free-form metadata dict, persisted to DB."""
        return self._job.meta or {}

    @meta.setter
    def meta(self, value: dict) -> None:
        self._job.meta = value

    def save_meta(self) -> None:
        """Persist meta dict to the database."""
        self._job.save_meta()

    def get_status(self) -> JobStatus:
        """Refresh status from DB and return."""
        self._job.refresh_from_db()
        return self.status

    def get_call_string(self) -> str:
        kwargs = getattr(self._job, "task_kwargs", {}) or {}
        return f"{self.func_name}(**{kwargs})"

    def __repr__(self) -> str:
        return f"<JobModel {self.name} status={self.status}>"


# ---------------------------------------------------------------------------
# Retry — RQ-compatible retry spec (translates to sqlery kwargs on enqueue)
# ---------------------------------------------------------------------------

class Retry:
    """RQ-compatible retry specification.

    Usage::

        from sqlery.compat.scheduler import Retry
        job = queue.enqueue(fn, retry=Retry(max=3, interval=5))
        job = queue.enqueue(fn, retry=Retry(max=3, interval=[5, 10, 20]))

    sqlery maps this to ``max_retries`` and ``retry_backoff``.  Because
    sqlery uses exponential backoff (``backoff * 2^attempt``) rather than a
    fixed-interval list, only the *first* interval value is used as the
    ``retry_backoff`` seed when a list is provided.
    """

    def __init__(self, max: int, interval: int | list[int] = 0):
        if max < 1:
            raise ValueError("max must be >= 1")
        self.max = max
        if isinstance(interval, int):
            self.intervals = [interval]
        else:
            self.intervals = list(interval)

    @property
    def retry_backoff(self) -> float:
        """First interval value used as sqlery retry_backoff seed."""
        return float(self.intervals[0]) if self.intervals else 0.0


# ---------------------------------------------------------------------------
# Callback — django-tasks-scheduler-compatible callback wrapper
# ---------------------------------------------------------------------------

@dataclass
class Callback:
    """Wraps a callable or dotted-path string for use as an on_success/on_failure hook.

    sqlery does not natively execute callbacks, but this class lets you
    carry callback references through the compat layer without import errors.
    """

    func: Callable | str
    timeout: int | None = None

    @property
    def name(self) -> str:
        if callable(self.func):
            return f"{self.func.__module__}.{self.func.__qualname__}"
        return str(self.func)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        fn = import_task(self.name) if isinstance(self.func, str) else self.func
        return fn(*args, **kwargs)


# ---------------------------------------------------------------------------
# get_current_job() — not supported; returns None with a warning
# ---------------------------------------------------------------------------

# def get_current_job() -> None:
#     """Stub for RQ's get_current_job() — returned None with a warning."""
#     logger.warning(
#         "get_current_job() is not supported in sqlery. "
#         "Refactor callers to accept job_id as a parameter."
#     )
#     return None

class _RQJobCompat:
    """Thin wrapper around QueuedJob exposing RQ-compatible properties.

    RQ's ``get_current_job()`` returned objects with:
      - ``.id``     — string job identifier (maps to ``job_name`` or str(pk))
      - ``.origin`` — queue name (maps to ``queue_name``)

    All other attribute access falls through to the underlying QueuedJob.
    """

    def __init__(self, qj):
        object.__setattr__(self, '_qj', qj)

    @property
    def id(self):
        """String job identifier, matching RQ's job.id convention."""
        return self._qj.job_name or str(self._qj.pk)

    @property
    def origin(self):
        """Queue name — RQ stored this as job.origin."""
        return self._qj.queue_name

    @property
    def connection(self):
        """RQ compat: Redis connection. Returns None since sqlery uses SQL, not Redis."""
        return None

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, '_qj'), name)

    def __setattr__(self, name, value):
        if name == '_qj':
            object.__setattr__(self, name, value)
        else:
            setattr(self._qj, name, value)

    def __bool__(self):
        return self._qj is not None

    def __repr__(self):
        return f"<_RQJobCompat job_name={self.id} queue={self.origin}>"


def get_current_job():
    """Return the QueuedJob currently executing in this context, or None.

    Returns an RQ-compatible wrapper so callers using ``.id`` (string) or
    ``.origin`` (queue name) work without modification.
    """
    # Check both the Django executor and core worker context vars
    from sqlery.core.worker import _current_job_var as core_var
    qj = core_var.get()
    if qj is None:
        from sqlery.django_sqlery.executor import _current_job_var
        qj = _current_job_var.get()
    if qj is None:
        return None
    return _RQJobCompat(qj)


# ---------------------------------------------------------------------------
# Queue — thin wrapper around sqlery's Queue for d-t-s API compatibility
# ---------------------------------------------------------------------------

class Queue:
    """Drop-in for django-tasks-scheduler's Queue.

    Maps ``create_and_enqueue_job()`` and common kwargs to sqlery's
    ``Queue.enqueue()`` / ``Queue.enqueue_at()`` / ``Queue.enqueue_in()``.
    """

    def __init__(self, name: str = "default", connection=None, **kwargs):
        self.name = name
        self._q = _SQLeryQueue(name, backend=_DjangoBackend())

    # --- Core enqueue ---

    def create_and_enqueue_job(
        self,
        func: Callable,
        args: tuple | list | None = None,
        kwargs: dict[str, Any] | None = None,
        when=None,
        timeout: int | None = None,
        result_ttl: int | None = None,
        job_info_ttl: int | None = None,
        description: str | None = None,
        name: str | None = None,
        at_front: bool = False,
        meta: dict[str, Any] | None = None,
        on_success=None,
        on_failure=None,
        on_stopped=None,
        retry: "Retry | None" = None,
        **extra,
    ) -> JobModel:
        """Enqueue a job, optionally scheduled for a future datetime.

        Args:
            func: Callable to execute.
            args: Positional arguments (merged into kwargs for sqlery).
            kwargs: Keyword arguments passed to func.
            when: Optional datetime — schedule for the future.
            timeout: Execution timeout in seconds.
            at_front: Enqueue at front (maps to priority=100).
            retry: Retry spec (Retry instance).

        Returns:
            JobModel wrapping the created QueuedJob.
        """
        merged_kwargs = dict(kwargs or {})
        if args:
            # sqlery passes everything as kwargs; positional args not directly
            # supported, so callers should prefer kwargs.
            logger.warning(
                "Queue.create_and_enqueue_job: positional 'args' are ignored by "
                "sqlery. Pass arguments via 'kwargs' instead."
            )

        enqueue_kwargs: dict[str, Any] = dict(extra)
        if timeout is not None:
            enqueue_kwargs["timeout_seconds"] = timeout
        if at_front:
            enqueue_kwargs["priority"] = 100
        if retry is not None:
            enqueue_kwargs["max_retries"] = retry.max
            if len(retry.intervals) == 1:
                enqueue_kwargs["retry_backoff"] = retry.intervals[0]
            else:
                enqueue_kwargs["retry_intervals"] = retry.intervals
        if name is not None:
            enqueue_kwargs["job_name"] = name

        if when is not None:
            queued = self._q.enqueue_at(when, func, **merged_kwargs, **enqueue_kwargs)
        else:
            queued = self._q.enqueue(func, **merged_kwargs, **enqueue_kwargs)

        job_model = JobModel(queued)
        if meta:
            queued.meta = dict(meta)
            queued.save_meta()
        return job_model

    def _map_rq_kwargs(self, kwargs: dict) -> dict:
        """Translate RQ-style enqueue kwargs to sqlery kwargs.

        Handles: job_id → job_name, args → _args, at_front → priority,
        retry → max_retries/retry_backoff, and silently drops RQ-only fields.
        """
        mapped: dict[str, Any] = {}

        rq_args = kwargs.pop("args", None)
        if rq_args is not None:
            mapped["_args"] = list(rq_args)

        job_id = kwargs.pop("job_id", None)
        if job_id is not None:
            mapped["job_name"] = str(job_id)

        retry: Retry | None = kwargs.pop("retry", None)
        if retry is not None:
            mapped.setdefault("max_retries", retry.max)
            if len(retry.intervals) == 1:
                mapped.setdefault("retry_backoff", retry.intervals[0])
            else:
                mapped.setdefault("retry_intervals", retry.intervals)

        # if kwargs.pop("at_front", False):
        #     mapped["priority"] = 100
        # Pass at_front through for queue.py's dynamic max_priority + 1 logic
        at_front = kwargs.pop("at_front", None)
        if at_front is not None:
            mapped["at_front"] = at_front

        for dropped in ("result_ttl", "failure_ttl", "description", "ttl", "timeout"):
            kwargs.pop(dropped, None)

        mapped.update(kwargs)
        return mapped

    def enqueue(self, func: Callable, *args, **kwargs) -> JobModel:
        """Enqueue immediately (RQ-style shorthand).

        Accepts RQ kwargs: job_id, args, at_front, retry, result_ttl, etc.
        """
        mapped = self._map_rq_kwargs(kwargs)
        if args and "_args" not in mapped:
            mapped["_args"] = list(args)
        return JobModel(self._q.enqueue(func, **mapped))

    def fetch_job(self, job_name: str) -> JobModel | None:
        """Look up a queued (not yet finished) job by its job_name string.

        Returns a JobModel if found, or None — mirrors RQ's Queue.fetch_job().
        """
        try:
            qj = QueuedJob.objects.filter(
                queue_name=self.name,
                job_name=job_name,
                status__in=("queued", "running"),
            ).first()
            return JobModel(qj) if qj else None
        except Exception:
            return None

    def enqueue_at(self, when, func: Callable, *args, **kwargs) -> JobModel:
        """Enqueue scheduled for a specific datetime."""
        mapped = self._map_rq_kwargs(kwargs)
        if args and "_args" not in mapped:
            mapped["_args"] = list(args)
        return JobModel(self._q.enqueue_at(when, func, **mapped))

    def enqueue_in(self, delay: timedelta, func: Callable, *args, **kwargs) -> JobModel:
        """Enqueue scheduled after a timedelta from now. Accepts RQ-style kwargs."""
        mapped = self._map_rq_kwargs(kwargs)
        if args and "_args" not in mapped:
            mapped["_args"] = list(args)
        return JobModel(self._q.enqueue_in(delay, func, **mapped))

    # --- Inspection ---

    def get_all_jobs(self) -> list[JobModel]:
        jobs = QueuedJob.objects.filter(queue_name=self.name).exclude(status="success")
        return [JobModel(j) for j in jobs]

    @property
    def job_ids(self) -> list[str]:
        """Return job_name strings for all queued jobs in this queue.

        Mirrors RQ's Queue.job_ids which returned string job IDs.
        Only returns ready-to-run jobs (not scheduled in the future),
        matching RQ's behavior where delayed jobs lived in a separate registry.
        """
        from django.db.models import Q
        from django.utils import timezone
        return list(
            QueuedJob.objects.filter(queue_name=self.name, status="queued")
            .filter(
                # RQ compat: only due jobs (not scheduled in the future)
                Q(scheduled_at__isnull=True) | Q(scheduled_at__lte=timezone.now())
            )
            .exclude(job_name="")
            .exclude(job_name__isnull=True)
            .values_list("job_name", flat=True)
        )

    @property
    def started_job_registry(self):
        """Stub for RQ's StartedJobRegistry — returns an object with get_job_ids()."""
        queue_name = self.name

        class _StartedRegistry:
            def get_job_ids(self) -> list[str]:
                return list(
                    QueuedJob.objects.filter(queue_name=queue_name, status="running")
                    .exclude(job_name="")
                    .exclude(job_name__isnull=True)
                    .values_list("job_name", flat=True)
                )
        return _StartedRegistry()

    @property
    def deferred_job_registry(self):
        """RQ compat: DeferredJobRegistry. sqlery has no separate deferred state;
        jobs waiting on dependencies stay as 'queued'. Returns empty — remove() is a no-op."""
        class _DeferredRegistry:
            def get_job_ids(self) -> list[str]:
                return []

            def remove(self, job_id: str) -> None:
                pass
        return _DeferredRegistry()

    @property
    def scheduled_job_registry(self):
        """RQ compat: ScheduledJobRegistry — future-scheduled queued jobs (scheduled_at > now).
        remove() cancels the job by deleting it from the queue."""
        queue_name = self.name

        class _ScheduledRegistry:
            def get_job_ids(self) -> list[str]:
                from django.utils import timezone
                return list(
                    QueuedJob.objects.filter(
                        queue_name=queue_name,
                        status="queued",
                        scheduled_at__gt=timezone.now(),
                    )
                    .exclude(job_name="")
                    .values_list("job_name", flat=True)
                )

            def remove(self, job_id: str) -> None:
                QueuedJob.objects.filter(
                    queue_name=queue_name, job_name=job_id, status="queued"
                ).delete()
        return _ScheduledRegistry()

    @property
    def canceled_job_registry(self):
        """RQ compat: CanceledJobRegistry — sqlery uses status='failed' for cancels."""
        class _CanceledRegistry:
            def get_job_ids(self) -> list[str]:
                return []

            def remove(self, job_id: str) -> None:
                pass
        return _CanceledRegistry()

    @property
    def finished_job_registry(self):
        """RQ compat: FinishedJobRegistry — maps to status='success'."""
        queue_name = self.name

        class _FinishedRegistry:
            def get_job_ids(self) -> list[str]:
                return list(
                    QueuedJob.objects.filter(queue_name=queue_name, status="success")
                    .exclude(job_name="")
                    .values_list("job_name", flat=True)
                )

            def remove(self, job_id: str) -> None:
                QueuedJob.objects.filter(
                    queue_name=queue_name, job_name=job_id, status="success"
                ).delete()
        return _FinishedRegistry()

    @property
    def failed_job_registry(self):
        """RQ compat: FailedJobRegistry — maps to status='failed'."""
        queue_name = self.name

        class _FailedRegistry:
            def get_job_ids(self) -> list[str]:
                return list(
                    QueuedJob.objects.filter(queue_name=queue_name, status="failed")
                    .exclude(job_name="")
                    .values_list("job_name", flat=True)
                )

            def remove(self, job_id: str) -> None:
                QueuedJob.objects.filter(
                    queue_name=queue_name, job_name=job_id, status="failed"
                ).delete()
        return _FailedRegistry()

    def cancel_job(self, job_name: str) -> None:
        pk = int(job_name)
        self._q.cancel_job(pk)

    def __repr__(self) -> str:
        return f"<Queue '{self.name}'>"


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def get_queue(name: str = "default") -> Queue:
    """Return a Queue instance for the named queue.

    Args:
        name: Queue name (default: 'default').

    Returns:
        Queue wrapper instance.
    """
    return Queue(name)


def get_all_workers() -> list:
    """Return all active Worker instances.

    Returns:
        List of Worker ORM objects (sqlery.django_sqlery.models.Worker).
    """
    return list(Worker.objects.filter(status__in=["idle", "busy"]))


# ---------------------------------------------------------------------------
# TaskType
# ---------------------------------------------------------------------------

class TaskType(str, Enum):
    """Schedule type enum matching django-tasks-scheduler naming."""

    CRON = "cron"
    REPEATABLE = "interval"  # d-t-s calls it REPEATABLE, SQLery calls it interval
    ONCE = "once"


# Field name mapping: d-t-s kwarg name -> ScheduledTask field name
_FIELD_MAP = {
    "callable": "task_path",
    "queue": "queue_name",
    "cron_string": "cron_expression",
}

# Fields from d-t-s / RQ that have no equivalent on ScheduledTask and must be
# silently dropped so that get_or_create / create don't blow up.
_IGNORED_FIELDS = {"job_id", "timeout", "result_ttl"}


def _translate_kwargs(kwargs: dict) -> dict:
    """Translate d-t-s field names to ScheduledTask field names."""
    translated = {}
    for key, value in kwargs.items():
        if key in _IGNORED_FIELDS:
            continue
        elif key == "task_type":
            # Convert TaskType enum to string value
            translated["schedule_type"] = value.value if isinstance(value, TaskType) else value
        elif key in _FIELD_MAP:
            translated[_FIELD_MAP[key]] = value
        else:
            translated[key] = value
    return translated


class TaskQuerySet:
    """Wraps a Django QuerySet to yield Task instances on iteration."""

    def __init__(self, queryset):
        self._qs = queryset

    def __iter__(self):
        for obj in self._qs:
            yield Task(obj)

    def __len__(self):
        return self._qs.count()

    def __bool__(self):
        return self._qs.exists()

    def count(self):
        return self._qs.count()

    def exists(self):
        return self._qs.exists()

    def first(self):
        obj = self._qs.first()
        return Task(obj) if obj else None

    def last(self):
        obj = self._qs.last()
        return Task(obj) if obj else None

    def order_by(self, *fields):
        return TaskQuerySet(self._qs.order_by(*fields))

    def delete(self):
        return self._qs.delete()


class TaskManager:
    """Proxy manager providing Task.objects.get/filter/all/create."""

    def get(self, **kwargs):
        translated = _translate_kwargs(kwargs)
        obj = _ScheduledTaskModel.objects.get(**translated)
        return Task(obj)

    def filter(self, **kwargs):
        translated = _translate_kwargs(kwargs)
        return TaskQuerySet(_ScheduledTaskModel.objects.filter(**translated))

    def all(self):
        return TaskQuerySet(_ScheduledTaskModel.objects.all())

    def create(self, **kwargs):
        translated = _translate_kwargs(kwargs)
        obj = _ScheduledTaskModel.objects.create(**translated)
        return Task(obj)

    def get_or_create(self, defaults=None, **kwargs):
        translated_lookup = _translate_kwargs(kwargs)
        translated_defaults = _translate_kwargs(defaults or {})
        obj, created = _ScheduledTaskModel.objects.get_or_create(
            defaults=translated_defaults, **translated_lookup
        )
        return Task(obj), created


class Task:
    """Composition-based wrapper around ScheduledTask for d-t-s compatibility."""

    objects = TaskManager()

    def __init__(self, task_or_kwargs=None, **kwargs):
        if isinstance(task_or_kwargs, _ScheduledTaskModel):
            self._task = task_or_kwargs
        else:
            # Merge positional dict with keyword args
            combined = {}
            if isinstance(task_or_kwargs, dict):
                combined.update(task_or_kwargs)
            combined.update(kwargs)
            translated = _translate_kwargs(combined)
            self._task = _ScheduledTaskModel(**translated)

        # Store timeout locally (passed to QueuedJob on enqueue)
        self._timeout = kwargs.get("timeout", None)

    # --- Field alias properties (read/write) ---

    @property
    def callable(self):
        return self._task.task_path

    @callable.setter
    def callable(self, value):
        self._task.task_path = value

    @property
    def queue(self):
        return self._task.queue_name

    @queue.setter
    def queue(self, value):
        self._task.queue_name = value

    @property
    def cron_string(self):
        return self._task.cron_expression

    @cron_string.setter
    def cron_string(self, value):
        self._task.cron_expression = value

    @property
    def task_type(self):
        return TaskType(self._task.schedule_type)

    @task_type.setter
    def task_type(self, value):
        self._task.schedule_type = value.value if isinstance(value, TaskType) else value

    @property
    def at_front(self):
        return self._task.priority >= 100

    @at_front.setter
    def at_front(self, value):
        self._task.priority = 100 if value else 0

    @property
    def timeout(self):
        return self._timeout

    @timeout.setter
    def timeout(self, value):
        self._timeout = value

    @property
    def result_ttl(self):
        return -1

    @result_ttl.setter
    def result_ttl(self, value):
        pass  # no-op

    # --- Pass-through properties (same name on both sides) ---

    @property
    def name(self):
        return self._task.name

    @name.setter
    def name(self, value):
        self._task.name = value

    @property
    def enabled(self):
        return self._task.enabled

    @enabled.setter
    def enabled(self, value):
        self._task.enabled = value

    @property
    def interval(self):
        return self._task.interval

    @interval.setter
    def interval(self, value):
        self._task.interval = value

    @property
    def interval_unit(self):
        return self._task.interval_unit

    @interval_unit.setter
    def interval_unit(self, value):
        self._task.interval_unit = value

    @property
    def repeat(self):
        return self._task.repeat

    @repeat.setter
    def repeat(self, value):
        self._task.repeat = value

    @property
    def scheduled_time(self):
        return self._task.scheduled_time

    @scheduled_time.setter
    def scheduled_time(self, value):
        self._task.scheduled_time = value

    @property
    def priority(self):
        return self._task.priority

    @priority.setter
    def priority(self, value):
        self._task.priority = value

    @property
    def task_kwargs(self):
        return self._task.task_kwargs

    @task_kwargs.setter
    def task_kwargs(self, value):
        self._task.task_kwargs = value

    # --- Computed properties (read-only, query QueuedJob) ---

    @property
    def successful_runs(self):
        return self._task.jobs.filter(status="success").count()

    @property
    def failed_runs(self):
        return self._task.jobs.filter(status="failed").count()

    @property
    def last_successful_run(self):
        job = self._task.jobs.filter(status="success").order_by("-finished_at").first()
        return job.finished_at if job else None

    @property
    def last_failed_run(self):
        job = self._task.jobs.filter(status="failed").order_by("-finished_at").first()
        return job.finished_at if job else None

    # --- Methods ---

    def enqueue_to_run(self):
        """Enqueue this task for immediate execution."""
        return enqueue_task(self._task)

    def unschedule(self):
        """Disable this task."""
        self._task.enabled = False
        self._task.save()

    def is_scheduled(self):
        """Check if this task is actively scheduled."""
        return self._task.enabled and self._task.next_run_at is not None

    def callable_func(self):
        """Import and return the task callable."""
        return import_task(self._task.task_path)

    def parse_args(self):
        """Return positional args (always empty in SQLery)."""
        return []

    def parse_kwargs(self):
        """Return keyword args dict."""
        return self._task.get_kwargs_dict()

    def to_dict(self):
        """Serialize to dict using d-t-s field names."""
        return {
            "name": self._task.name,
            "callable": self._task.task_path,
            "queue": self._task.queue_name,
            "cron_string": self._task.cron_expression,
            "task_type": self._task.schedule_type,
            "enabled": self._task.enabled,
            "priority": self._task.priority,
            "interval": self._task.interval,
            "interval_unit": self._task.interval_unit,
            "repeat": self._task.repeat,
            "scheduled_time": self._task.scheduled_time,
            "task_kwargs": self._task.task_kwargs,
            "timeout": self._timeout,
            "result_ttl": -1,
        }

    def interval_seconds(self):
        """Get interval in seconds."""
        return self._task.get_interval_seconds()

    def _schedule(self):
        """Recalculate next_run_at and save."""
        self._task.next_run_at = self._task._calculate_next_run()
        self._task.save()

    def clean(self):
        """Validate via ScheduledTask.clean()."""
        self._task.clean()

    def save(self, **kwargs):
        """Save via ScheduledTask.save()."""
        self._task.save(**kwargs)

    def delete(self, **kwargs):
        """Delete via ScheduledTask.delete()."""
        self._task.delete(**kwargs)

    @property
    def pk(self):
        return self._task.pk

    @property
    def id(self):
        return self._task.id

    def __repr__(self):
        return f"<Task: {self._task.name}>"

    def __str__(self):
        return self._task.name


@dataclass
class TaskArg:
    """Import compatibility stub. SQLery uses task_kwargs JSONField."""

    val: str = ""
    content_type: str = "str"


@dataclass
class TaskKwarg:
    """Import compatibility stub. SQLery uses task_kwargs JSONField."""

    key: str = ""
    val: str = ""
    content_type: str = "str"


def get_scheduled_task(name: str) -> Task:
    """Get a scheduled task by name, wrapped as a Task.

    Args:
        name: Unique task name

    Returns:
        Task wrapper instance
    """
    obj = _ScheduledTaskModel.objects.get(name=name)
    return Task(obj)


def run_task(name: str) -> QueuedJob:
    """Run a scheduled task immediately by name.

    Args:
        name: Unique task name

    Returns:
        QueuedJob created by enqueueing the task
    """
    obj = _ScheduledTaskModel.objects.get(name=name)
    return enqueue_task(obj)


def get_next_cron_time(cron_string: str):
    """Calculate the next occurrence for a cron expression.

    Args:
        cron_string: Cron expression (e.g., '0 2 * * *')

    Returns:
        datetime: Next occurrence in UTC
    """
    return calculate_next_run(cron_string)


# d-t-s model aliases: in sqlery all three are the same Task,
# but callers can keep using the old class names unchanged.
CronTask = Task
RepeatableTask = Task
# NOTE: name intentionally shadows django_sqlery.models.ScheduledTask;
# callers that imported `from scheduler.models import ScheduledTask`
# now get the compat Task wrapper instead.
ScheduledTask = Task


__all__ = [
    # Task model + enum
    "Task",
    "TaskType",
    "TaskArg",
    "TaskKwarg",
    # d-t-s model aliases
    "CronTask",
    "RepeatableTask",
    "ScheduledTask",
    # Scheduled task helpers
    "get_scheduled_task",
    "run_task",
    "get_next_cron_time",
    # Job model + status
    "JobModel",
    "JobStatus",
    # Queue API
    "Queue",
    "get_queue",
    "get_all_workers",
    # RQ-compat extras
    "Retry",
    "Callback",
    "get_current_job",
    # Decorator
    "job",
]
