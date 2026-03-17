"""Tests for django-tasks-scheduler compatibility layer."""

import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.settings")
django.setup()

import pytest
from django.test import TestCase
from django.utils import timezone

from datetime import timedelta

from sqlery.compat.scheduler import (
    Queue,
    Task,
    TaskArg,
    TaskKwarg,
    TaskType,
    get_next_cron_time,
    get_scheduled_task,
    job,
    run_task,
)
from sqlery.django_sqlery.models import QueuedJob, ScheduledTask


class TestTaskTypeEnum(TestCase):
    """TaskType enum values and mapping to schedule_type."""

    def test_cron_value(self):
        assert TaskType.CRON.value == "cron"

    def test_repeatable_maps_to_interval(self):
        assert TaskType.REPEATABLE.value == "interval"

    def test_once_value(self):
        assert TaskType.ONCE.value == "once"

    def test_enum_is_str(self):
        assert isinstance(TaskType.CRON, str)
        assert TaskType.CRON == "cron"


class TestTaskCreation(TestCase):
    """Task creation with d-t-s field names."""

    def test_create_with_dts_kwargs(self):
        task = Task(
            name="test-task",
            callable="myapp.tasks.foo",
            task_type=TaskType.CRON,
            cron_string="* * * * *",
            queue="high",
        )
        assert task.name == "test-task"
        assert task.callable == "myapp.tasks.foo"
        assert task.task_type == TaskType.CRON
        assert task.cron_string == "* * * * *"
        assert task.queue == "high"

    def test_create_from_scheduled_task(self):
        st = ScheduledTask(
            name="from-st",
            task_path="myapp.tasks.bar",
            schedule_type="cron",
            cron_expression="0 2 * * *",
        )
        task = Task(st)
        assert task.name == "from-st"
        assert task.callable == "myapp.tasks.bar"
        assert task.cron_string == "0 2 * * *"

    def test_create_interval_task(self):
        task = Task(
            name="interval-task",
            callable="myapp.tasks.baz",
            task_type=TaskType.REPEATABLE,
            interval=5,
            interval_unit="minutes",
        )
        assert task.task_type == TaskType.REPEATABLE
        assert task.interval == 5
        assert task.interval_unit == "minutes"


class TestTaskFieldAliases(TestCase):
    """Task field aliases (callable, queue, cron_string, task_type)."""

    def setUp(self):
        self.task = Task(
            name="alias-test",
            callable="myapp.tasks.foo",
            task_type=TaskType.CRON,
            cron_string="0 * * * *",
            queue="default",
        )

    def test_callable_read_write(self):
        assert self.task.callable == "myapp.tasks.foo"
        self.task.callable = "myapp.tasks.bar"
        assert self.task.callable == "myapp.tasks.bar"
        assert self.task._task.task_path == "myapp.tasks.bar"

    def test_queue_read_write(self):
        assert self.task.queue == "default"
        self.task.queue = "urgent"
        assert self.task.queue == "urgent"
        assert self.task._task.queue_name == "urgent"

    def test_cron_string_read_write(self):
        assert self.task.cron_string == "0 * * * *"
        self.task.cron_string = "30 2 * * *"
        assert self.task.cron_string == "30 2 * * *"
        assert self.task._task.cron_expression == "30 2 * * *"

    def test_task_type_read_write(self):
        assert self.task.task_type == TaskType.CRON
        self.task.task_type = TaskType.REPEATABLE
        assert self.task.task_type == TaskType.REPEATABLE
        assert self.task._task.schedule_type == "interval"

    def test_at_front_read_write(self):
        assert self.task.at_front is False
        self.task.at_front = True
        assert self.task.at_front is True
        assert self.task.priority >= 100

    def test_result_ttl_noop(self):
        assert self.task.result_ttl == -1
        self.task.result_ttl = 3600  # should be ignored
        assert self.task.result_ttl == -1


class TestTaskManager(TestCase):
    """Task.objects.get(), .filter(), .all(), .create() return Task wrappers."""

    def test_create_returns_task(self):
        task = Task.objects.create(
            name="mgr-create",
            callable="myapp.tasks.foo",
            cron_string="* * * * *",
        )
        assert isinstance(task, Task)
        assert task.name == "mgr-create"
        assert task.pk is not None

    def test_get_returns_task(self):
        Task.objects.create(
            name="mgr-get",
            callable="myapp.tasks.foo",
            cron_string="* * * * *",
        )
        task = Task.objects.get(name="mgr-get")
        assert isinstance(task, Task)
        assert task.callable == "myapp.tasks.foo"

    def test_filter_returns_task_queryset(self):
        Task.objects.create(
            name="mgr-filter-1",
            callable="myapp.tasks.foo",
            cron_string="* * * * *",
            queue="high",
        )
        Task.objects.create(
            name="mgr-filter-2",
            callable="myapp.tasks.bar",
            cron_string="* * * * *",
            queue="high",
        )
        results = Task.objects.filter(queue="high")
        assert results.count() == 2
        for task in results:
            assert isinstance(task, Task)

    def test_all_returns_task_queryset(self):
        Task.objects.create(
            name="mgr-all-1",
            callable="myapp.tasks.foo",
            cron_string="* * * * *",
        )
        results = Task.objects.all()
        assert results.count() >= 1

    def test_filter_by_task_type(self):
        Task.objects.create(
            name="mgr-type-filter",
            callable="myapp.tasks.foo",
            cron_string="* * * * *",
            task_type=TaskType.CRON,
        )
        results = Task.objects.filter(task_type=TaskType.CRON)
        assert results.exists()

    def test_queryset_first_last(self):
        Task.objects.create(
            name="mgr-fl-1",
            callable="myapp.tasks.foo",
            cron_string="* * * * *",
        )
        Task.objects.create(
            name="mgr-fl-2",
            callable="myapp.tasks.bar",
            cron_string="* * * * *",
        )
        results = Task.objects.all()
        first = results.first()
        last = results.last()
        assert isinstance(first, Task)
        assert isinstance(last, Task)

    def test_queryset_order_by(self):
        Task.objects.create(
            name="mgr-ob-b",
            callable="myapp.tasks.foo",
            cron_string="* * * * *",
        )
        Task.objects.create(
            name="mgr-ob-a",
            callable="myapp.tasks.bar",
            cron_string="* * * * *",
        )
        results = Task.objects.all().order_by("name")
        names = [t.name for t in results]
        assert names == sorted(names)


class TestTaskEnqueue(TestCase):
    """Task.enqueue_to_run() creates QueuedJob."""

    def test_enqueue_creates_job(self):
        task = Task.objects.create(
            name="enqueue-test",
            callable="tests.test_scheduler_compat.dummy_task",
            cron_string="* * * * *",
        )
        job = task.enqueue_to_run()
        assert isinstance(job, QueuedJob)
        assert job.task_path == "tests.test_scheduler_compat.dummy_task"
        assert job.scheduled_task_id == task.pk


class TestTaskSave(TestCase):
    """Task.save() persists via ScheduledTask."""

    def test_save_persists(self):
        task = Task(
            name="save-test",
            callable="myapp.tasks.foo",
            task_type=TaskType.CRON,
            cron_string="0 * * * *",
        )
        task.save()
        assert task.pk is not None

        fetched = Task.objects.get(name="save-test")
        assert fetched.callable == "myapp.tasks.foo"

    def test_delete_removes(self):
        task = Task.objects.create(
            name="delete-test",
            callable="myapp.tasks.foo",
            cron_string="* * * * *",
        )
        pk = task.pk
        task.delete()
        assert not ScheduledTask.objects.filter(pk=pk).exists()


class TestUtilityFunctions(TestCase):
    """get_scheduled_task() and run_task() utility functions."""

    def test_get_scheduled_task(self):
        ScheduledTask.objects.create(
            name="util-get",
            task_path="myapp.tasks.foo",
            cron_expression="* * * * *",
        )
        task = get_scheduled_task("util-get")
        assert isinstance(task, Task)
        assert task.name == "util-get"

    def test_run_task_creates_job(self):
        ScheduledTask.objects.create(
            name="util-run",
            task_path="tests.test_scheduler_compat.dummy_task",
            cron_expression="* * * * *",
        )
        job = run_task("util-run")
        assert isinstance(job, QueuedJob)
        assert job.task_path == "tests.test_scheduler_compat.dummy_task"


class TestGetNextCronTime(TestCase):
    """get_next_cron_time() returns valid datetime."""

    def test_returns_datetime(self):
        result = get_next_cron_time("0 2 * * *")
        assert result is not None
        assert result.tzinfo is not None

    def test_is_in_future(self):
        result = get_next_cron_time("* * * * *")
        assert result >= timezone.now()


class TestTaskToDict(TestCase):
    """Task.to_dict() uses d-t-s field names."""

    def test_to_dict_field_names(self):
        task = Task(
            name="dict-test",
            callable="myapp.tasks.foo",
            task_type=TaskType.CRON,
            cron_string="0 * * * *",
            queue="default",
        )
        d = task.to_dict()
        assert d["name"] == "dict-test"
        assert d["callable"] == "myapp.tasks.foo"
        assert d["queue"] == "default"
        assert d["cron_string"] == "0 * * * *"
        assert d["task_type"] == "cron"
        assert d["result_ttl"] == -1
        assert "task_path" not in d
        assert "queue_name" not in d
        assert "cron_expression" not in d


class TestComputedStats(TestCase):
    """Computed stats fields (successful_runs, failed_runs)."""

    def test_initial_counts_zero(self):
        task = Task.objects.create(
            name="stats-test",
            callable="myapp.tasks.foo",
            cron_string="* * * * *",
        )
        assert task.successful_runs == 0
        assert task.failed_runs == 0
        assert task.last_successful_run is None
        assert task.last_failed_run is None

    def test_counts_after_jobs(self):
        st = ScheduledTask.objects.create(
            name="stats-jobs",
            task_path="myapp.tasks.foo",
            cron_expression="* * * * *",
        )
        now = timezone.now()
        QueuedJob.objects.create(
            task_path="myapp.tasks.foo",
            scheduled_task=st,
            status="success",
            finished_at=now,
        )
        QueuedJob.objects.create(
            task_path="myapp.tasks.foo",
            scheduled_task=st,
            status="failed",
            finished_at=now,
        )
        QueuedJob.objects.create(
            task_path="myapp.tasks.foo",
            scheduled_task=st,
            status="success",
            finished_at=now,
        )

        task = Task(st)
        assert task.successful_runs == 2
        assert task.failed_runs == 1


class TestTaskArgKwarg(TestCase):
    """TaskArg/TaskKwarg importable."""

    def test_task_arg_defaults(self):
        arg = TaskArg()
        assert arg.val == ""
        assert arg.content_type == "str"

    def test_task_kwarg_defaults(self):
        kw = TaskKwarg()
        assert kw.key == ""
        assert kw.val == ""
        assert kw.content_type == "str"

    def test_task_arg_with_values(self):
        arg = TaskArg(val="42", content_type="int")
        assert arg.val == "42"
        assert arg.content_type == "int"

    def test_task_kwarg_with_values(self):
        kw = TaskKwarg(key="count", val="10", content_type="int")
        assert kw.key == "count"
        assert kw.val == "10"


class TestTaskMethods(TestCase):
    """Additional Task method tests."""

    def test_unschedule(self):
        task = Task.objects.create(
            name="unsched-test",
            callable="myapp.tasks.foo",
            cron_string="* * * * *",
        )
        assert task.enabled is True
        task.unschedule()
        fetched = Task.objects.get(name="unsched-test")
        assert fetched.enabled is False

    def test_is_scheduled(self):
        task = Task.objects.create(
            name="is-sched-test",
            callable="myapp.tasks.foo",
            cron_string="* * * * *",
        )
        assert task.is_scheduled() is True
        task.unschedule()
        assert task.is_scheduled() is False

    def test_parse_args_empty(self):
        task = Task(
            name="pa-test",
            callable="myapp.tasks.foo",
            cron_string="* * * * *",
        )
        assert task.parse_args() == []

    def test_parse_kwargs(self):
        task = Task(
            name="pk-test",
            callable="myapp.tasks.foo",
            cron_string="* * * * *",
            task_kwargs={"key": "value"},
        )
        assert task.parse_kwargs() == {"key": "value"}

    def test_interval_seconds(self):
        task = Task(
            name="isec-test",
            callable="myapp.tasks.foo",
            task_type=TaskType.REPEATABLE,
            interval=5,
            interval_unit="minutes",
        )
        assert task.interval_seconds() == 300

    def test_repr_and_str(self):
        task = Task(
            name="repr-test",
            callable="myapp.tasks.foo",
            cron_string="* * * * *",
        )
        assert "repr-test" in repr(task)
        assert str(task) == "repr-test"


class TestJobDecorator(TestCase):
    """The job decorator is re-exported correctly."""

    def test_job_is_callable(self):
        assert callable(job)

    def test_job_decorator_works(self):
        @job
        def my_task():
            return 42

        assert my_task() == 42
        assert hasattr(my_task, "enqueue")


class TestQueueAtFront(TestCase):
    """at_front=True through the compat layer results in dynamic priority."""

    def test_at_front_sets_priority_above_max(self):
        """at_front=True should set priority to max_priority + 1."""
        # Seed a normal-priority job
        QueuedJob.objects.create(
            task_path="tests.test_scheduler_compat.dummy_task",
            queue_name="default",
            status="queued",
            priority=0,
            job_name="seed-job",
        )
        queue = Queue("default")
        job_model = queue.enqueue(
            dummy_task,
            job_id="front-job",
            at_front=True,
        )
        qj = QueuedJob.objects.get(job_name="front-job")
        assert qj.priority >= 1, f"Expected priority >= 1, got {qj.priority}"

    def test_at_front_false_keeps_default_priority(self):
        """at_front=False should not bump priority."""
        queue = Queue("default")
        job_model = queue.enqueue(
            dummy_task,
            job_id="normal-job",
            at_front=False,
        )
        qj = QueuedJob.objects.get(job_name="normal-job")
        assert qj.priority == 0, f"Expected priority 0, got {qj.priority}"

    def test_at_front_stacks_above_previous(self):
        """Multiple at_front jobs should each stack above the previous max."""
        queue = Queue("default")
        queue.enqueue(dummy_task, job_id="front-1", at_front=True)
        queue.enqueue(dummy_task, job_id="front-2", at_front=True)
        p1 = QueuedJob.objects.get(job_name="front-1").priority
        p2 = QueuedJob.objects.get(job_name="front-2").priority
        assert p2 > p1, f"Expected front-2 priority ({p2}) > front-1 ({p1})"


class TestQueueJobIdsExcludesScheduled(TestCase):
    """queue.job_ids excludes future-scheduled jobs (RQ compat)."""

    def test_job_ids_excludes_future_scheduled(self):
        """Jobs with scheduled_at in the future should not appear in job_ids."""
        future = timezone.now() + timedelta(hours=1)
        QueuedJob.objects.create(
            task_path="tests.test_scheduler_compat.dummy_task",
            queue_name="default",
            status="queued",
            job_name="future-job",
            scheduled_at=future,
        )
        queue = Queue("default")
        assert "future-job" not in queue.job_ids

    def test_job_ids_includes_past_scheduled(self):
        """Jobs with scheduled_at in the past should appear in job_ids."""
        past = timezone.now() - timedelta(hours=1)
        QueuedJob.objects.create(
            task_path="tests.test_scheduler_compat.dummy_task",
            queue_name="default",
            status="queued",
            job_name="past-job",
            scheduled_at=past,
        )
        queue = Queue("default")
        assert "past-job" in queue.job_ids

    def test_job_ids_includes_no_scheduled_at(self):
        """Jobs with no scheduled_at should appear in job_ids."""
        QueuedJob.objects.create(
            task_path="tests.test_scheduler_compat.dummy_task",
            queue_name="default",
            status="queued",
            job_name="immediate-job",
            scheduled_at=None,
        )
        queue = Queue("default")
        assert "immediate-job" in queue.job_ids


# Dummy task for enqueue tests
def dummy_task():
    return "done"
