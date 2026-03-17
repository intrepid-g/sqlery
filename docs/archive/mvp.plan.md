# Sqlery - MVP Implementation Plan

## Overview

Build a production-ready, LEAN Django package for cron-based job scheduling that works **without separate scheduler/worker processes** in both traditional and serverless environments.

---

## Project Structure

```
sqlery/
├── pyproject.toml
├── README.md
├── LICENSE
├── src/
│   └── sqlery/
│       ├── __init__.py
│       ├── apps.py
│       ├── models.py              # ScheduledTask, TaskExecution
│       ├── admin.py               # Django Admin configuration
│       ├── executor.py            # Core execution engine
│       ├── triggers.py            # Trigger mechanisms
│       ├── middleware.py          # Request-based trigger
│       ├── utils.py               # Cron parsing, task import
│       ├── settings.py            # Package settings
│       ├── migrations/
│       │   └── 0001_initial.py
│       └── management/
│           └── commands/
│               └── run_scheduled_tasks.py
├── tests/
│   ├── __init__.py
│   ├── test_models.py
│   ├── test_executor.py
│   ├── test_triggers.py
│   └── test_integration.py
└── examples/
    └── demo_project/
        ├── manage.py
        ├── demo/
        │   ├── settings.py
        │   └── tasks.py
        └── requirements.txt
```

---

## Phase 1: Core Models

### ScheduledTask Model

```python
# models.py
from django.db import models
from django.utils import timezone

class ScheduledTask(models.Model):
    """A scheduled task that runs on a cron schedule."""

    # Task definition
    name = models.CharField(
        max_length=255,
        unique=True,
        help_text="Unique name for this task"
    )
    task_path = models.CharField(
        max_length=500,
        help_text="Python path to callable (e.g., 'myapp.tasks.my_function')"
    )
    cron_expression = models.CharField(
        max_length=100,
        help_text="Cron expression (e.g., '0 2 * * *' for 2 AM daily)"
    )

    # Status
    enabled = models.BooleanField(
        default=True,
        help_text="Whether this task should run"
    )

    # Execution tracking
    last_run_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last successful execution time (UTC)"
    )
    next_run_at = models.DateTimeField(
        help_text="Next scheduled execution time (UTC)"
    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'sqlery_scheduled_task'
        ordering = ['name']
        indexes = [
            models.Index(fields=['enabled', 'next_run_at']),  # For efficient queries
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        """Calculate next_run_at if not set."""
        if not self.next_run_at:
            from .utils import calculate_next_run
            self.next_run_at = calculate_next_run(self.cron_expression)
        super().save(*args, **kwargs)
```

### TaskExecution Model

```python
class TaskExecution(models.Model):
    """Record of a task execution."""

    STATUS_CHOICES = [
        ('running', 'Running'),
        ('success', 'Success'),
        ('failed', 'Failed'),
    ]

    task = models.ForeignKey(
        ScheduledTask,
        on_delete=models.CASCADE,
        related_name='executions'
    )
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='running'
    )

    # Timing
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)

    # Results
    output = models.TextField(
        blank=True,
        help_text="Task return value or stdout"
    )
    error = models.TextField(
        blank=True,
        help_text="Error message if failed"
    )
    traceback = models.TextField(
        blank=True,
        help_text="Full traceback if failed"
    )

    class Meta:
        db_table = 'sqlery_task_execution'
        ordering = ['-started_at']
        indexes = [
            models.Index(fields=['task', 'status']),
            models.Index(fields=['started_at']),
        ]

    def __str__(self):
        return f"{self.task.name} - {self.started_at} - {self.status}"

    def mark_success(self, output=''):
        """Mark execution as successful."""
        from django.utils import timezone
        self.status = 'success'
        self.finished_at = timezone.now()
        self.duration_seconds = (self.finished_at - self.started_at).total_seconds()
        self.output = str(output)
        self.save()

    def mark_failed(self, error, traceback=''):
        """Mark execution as failed."""
        from django.utils import timezone
        self.status = 'failed'
        self.finished_at = timezone.now()
        self.duration_seconds = (self.finished_at - self.started_at).total_seconds()
        self.error = str(error)
        self.traceback = traceback
        self.save()
```

---

## Phase 2: Utilities

### Cron Parsing

```python
# utils.py
from datetime import datetime, timezone as dt_timezone
from croniter import croniter

def calculate_next_run(cron_expression, base_time=None):
    """Calculate next run time from cron expression.

    Args:
        cron_expression: Cron string like "0 2 * * *"
        base_time: Base datetime (defaults to now UTC)

    Returns:
        datetime: Next run time in UTC
    """
    if base_time is None:
        base_time = datetime.now(dt_timezone.utc)

    cron = croniter(cron_expression, base_time)
    next_run = cron.get_next(datetime)

    # Ensure UTC
    if next_run.tzinfo is None:
        next_run = next_run.replace(tzinfo=dt_timezone.utc)

    return next_run


def validate_cron_expression(cron_expression):
    """Validate cron expression.

    Returns:
        tuple: (is_valid, error_message)
    """
    try:
        croniter(cron_expression)
        return True, None
    except Exception as e:
        return False, str(e)
```

### Task Import

```python
def import_task(task_path):
    """Import a callable from a string path.

    Args:
        task_path: String like "myapp.tasks.my_function"

    Returns:
        callable: The imported function

    Raises:
        ImportError: If task cannot be imported
    """
    from importlib import import_module

    try:
        module_path, function_name = task_path.rsplit('.', 1)
        module = import_module(module_path)
        task_func = getattr(module, function_name)

        if not callable(task_func):
            raise ImportError(f"{task_path} is not callable")

        return task_func
    except (ValueError, ImportError, AttributeError) as e:
        raise ImportError(f"Cannot import task '{task_path}': {e}")
```

---

## Phase 3: Execution Engine

### Core Executor

```python
# executor.py
import logging
import traceback as tb
from django.db import transaction
from django.utils import timezone
from .models import ScheduledTask, TaskExecution
from .utils import import_task, calculate_next_run

logger = logging.getLogger(__name__)


class TaskExecutor:
    """Executes scheduled tasks."""

    def get_due_tasks(self):
        """Get all enabled tasks that are due to run.

        Returns:
            QuerySet: Tasks where next_run_at <= now
        """
        now = timezone.now()
        return ScheduledTask.objects.filter(
            enabled=True,
            next_run_at__lte=now
        )

    def can_execute(self, task):
        """Check if task can be executed (not already running).

        Args:
            task: ScheduledTask instance

        Returns:
            bool: True if task can execute
        """
        # Check for already running executions
        has_running = TaskExecution.objects.filter(
            task=task,
            status='running'
        ).exists()

        return not has_running

    def execute_task(self, task):
        """Execute a single task.

        Args:
            task: ScheduledTask instance

        Returns:
            TaskExecution: The execution record
        """
        # Check concurrency
        if not self.can_execute(task):
            logger.info(f"Task '{task.name}' already running, skipping")
            return None

        # Create execution record
        execution = TaskExecution.objects.create(task=task)

        try:
            # Import task function
            task_func = import_task(task.task_path)

            # Execute
            logger.info(f"Executing task: {task.name}")
            result = task_func()

            # Mark success
            execution.mark_success(output=result)
            logger.info(f"Task '{task.name}' completed successfully")

        except Exception as e:
            # Mark failed
            error_msg = str(e)
            error_traceback = tb.format_exc()
            execution.mark_failed(error=error_msg, traceback=error_traceback)
            logger.error(f"Task '{task.name}' failed: {error_msg}")

        finally:
            # Update next run time
            with transaction.atomic():
                task.refresh_from_db()
                task.last_run_at = execution.started_at
                task.next_run_at = calculate_next_run(
                    task.cron_expression,
                    base_time=timezone.now()
                )
                task.save()

        return execution

    def run_due_tasks(self):
        """Find and execute all due tasks.

        Returns:
            list: TaskExecution instances created
        """
        due_tasks = self.get_due_tasks()
        executions = []

        logger.info(f"Found {due_tasks.count()} due tasks")

        for task in due_tasks:
            execution = self.execute_task(task)
            if execution:
                executions.append(execution)

        return executions
```

---

## Phase 4: Trigger Mechanisms

### Middleware (Traditional Deployment)

```python
# middleware.py
from django.core.cache import cache
from django.utils import timezone
import logging

logger = logging.getLogger(__name__)


class ScheduledTaskMiddleware:
    """Middleware to trigger scheduled tasks on requests.

    Checks for due tasks periodically (throttled by cache).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Check if we should trigger tasks
        self.maybe_trigger_tasks()

        response = self.get_response(request)
        return response

    def maybe_trigger_tasks(self):
        """Check and trigger tasks if needed (throttled)."""
        from .settings import get_setting
        from .triggers import trigger_due_tasks

        # Check if enabled
        if not get_setting('ENABLE_MIDDLEWARE_TRIGGER', True):
            return

        # Throttle checks (don't check on every request)
        check_interval = get_setting('CHECK_INTERVAL_SECONDS', 60)
        cache_key = 'sqlery:last_check'

        if cache.get(cache_key):
            return  # Already checked recently

        # Set cache for next interval
        cache.set(cache_key, True, check_interval)

        # Trigger tasks asynchronously
        try:
            trigger_due_tasks()
        except Exception as e:
            logger.error(f"Failed to trigger tasks: {e}")
```

### Management Command (Serverless/Manual)

```python
# management/commands/run_scheduled_tasks.py
from django.core.management.base import BaseCommand
from sqlery.executor import TaskExecutor


class Command(BaseCommand):
    help = 'Run all due scheduled tasks'

    def add_arguments(self, parser):
        parser.add_argument(
            '--task',
            type=str,
            help='Run specific task by name',
        )

    def handle(self, *args, **options):
        executor = TaskExecutor()

        if options['task']:
            # Run specific task
            from sqlery.models import ScheduledTask
            try:
                task = ScheduledTask.objects.get(name=options['task'], enabled=True)
                execution = executor.execute_task(task)
                if execution:
                    self.stdout.write(
                        self.style.SUCCESS(f"Executed '{task.name}': {execution.status}")
                    )
                else:
                    self.stdout.write(
                        self.style.WARNING(f"Task '{task.name}' already running")
                    )
            except ScheduledTask.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(f"Task '{options['task']}' not found or disabled")
                )
        else:
            # Run all due tasks
            executions = executor.run_due_tasks()
            self.stdout.write(
                self.style.SUCCESS(f"Executed {len(executions)} tasks")
            )
```

### Trigger Helper

```python
# triggers.py
import logging

logger = logging.getLogger(__name__)


def trigger_due_tasks():
    """Trigger execution of due tasks.

    Uses django-tasks if available, otherwise runs synchronously.
    """
    from .settings import get_setting

    use_django_tasks = get_setting('USE_DJANGO_TASKS', True)

    if use_django_tasks:
        try:
            from django_tasks import task

            @task()
            def run_tasks():
                from .executor import TaskExecutor
                executor = TaskExecutor()
                return executor.run_due_tasks()

            run_tasks()
            logger.info("Triggered tasks via django-tasks")

        except ImportError:
            logger.warning("django-tasks not installed, running synchronously")
            _run_synchronously()
    else:
        _run_synchronously()


def _run_synchronously():
    """Run tasks synchronously (blocking)."""
    from .executor import TaskExecutor
    executor = TaskExecutor()
    executor.run_due_tasks()
    logger.info("Ran tasks synchronously")
```

---

## Phase 5: Django Admin

### Admin Configuration

```python
# admin.py
from django.contrib import admin
from django.utils.html import format_html
from django.utils import timezone
from .models import ScheduledTask, TaskExecution


@admin.register(ScheduledTask)
class ScheduledTaskAdmin(admin.ModelAdmin):
    list_display = [
        'name',
        'enabled_status',
        'cron_expression',
        'last_run_display',
        'next_run_display',
        'execution_count',
        'actions_column',
    ]
    list_filter = ['enabled', 'created_at']
    search_fields = ['name', 'task_path']
    readonly_fields = ['last_run_at', 'next_run_at', 'created_at', 'updated_at']

    fieldsets = (
        ('Task Definition', {
            'fields': ('name', 'task_path', 'cron_expression', 'enabled')
        }),
        ('Execution Info', {
            'fields': ('last_run_at', 'next_run_at')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def enabled_status(self, obj):
        if obj.enabled:
            return format_html('<span style="color: green;">✓ Enabled</span>')
        return format_html('<span style="color: red;">✗ Disabled</span>')
    enabled_status.short_description = 'Status'

    def last_run_display(self, obj):
        if obj.last_run_at:
            return obj.last_run_at.strftime('%Y-%m-%d %H:%M UTC')
        return '-'
    last_run_display.short_description = 'Last Run'

    def next_run_display(self, obj):
        now = timezone.now()
        if obj.next_run_at:
            if obj.next_run_at <= now:
                return format_html(
                    '<span style="color: orange;">⏰ {} (due)</span>',
                    obj.next_run_at.strftime('%Y-%m-%d %H:%M UTC')
                )
            return obj.next_run_at.strftime('%Y-%m-%d %H:%M UTC')
        return '-'
    next_run_display.short_description = 'Next Run'

    def execution_count(self, obj):
        total = obj.executions.count()
        failed = obj.executions.filter(status='failed').count()
        if failed > 0:
            return format_html('{} total ({} failed)', total, failed)
        return total
    execution_count.short_description = 'Executions'

    def actions_column(self, obj):
        return format_html(
            '<a class="button" href="{}">View History</a>',
            f'/admin/sqlery/taskexecution/?task__id__exact={obj.id}'
        )
    actions_column.short_description = 'Actions'

    actions = ['run_now', 'enable_tasks', 'disable_tasks']

    def run_now(self, request, queryset):
        """Admin action to run tasks immediately."""
        from .executor import TaskExecutor
        executor = TaskExecutor()
        count = 0
        for task in queryset.filter(enabled=True):
            execution = executor.execute_task(task)
            if execution:
                count += 1
        self.message_user(request, f"Triggered {count} tasks")
    run_now.short_description = "Run selected tasks now"

    def enable_tasks(self, request, queryset):
        updated = queryset.update(enabled=True)
        self.message_user(request, f"Enabled {updated} tasks")
    enable_tasks.short_description = "Enable selected tasks"

    def disable_tasks(self, request, queryset):
        updated = queryset.update(enabled=False)
        self.message_user(request, f"Disabled {updated} tasks")
    disable_tasks.short_description = "Disable selected tasks"


@admin.register(TaskExecution)
class TaskExecutionAdmin(admin.ModelAdmin):
    list_display = [
        'task',
        'status_display',
        'started_at',
        'duration_display',
        'output_preview',
    ]
    list_filter = ['status', 'started_at', 'task']
    search_fields = ['task__name', 'output', 'error']
    readonly_fields = [
        'task', 'status', 'started_at', 'finished_at',
        'duration_seconds', 'output', 'error', 'traceback'
    ]

    fieldsets = (
        ('Execution Info', {
            'fields': ('task', 'status', 'started_at', 'finished_at', 'duration_seconds')
        }),
        ('Results', {
            'fields': ('output', 'error', 'traceback')
        }),
    )

    def has_add_permission(self, request):
        return False  # Can't manually create executions

    def status_display(self, obj):
        colors = {
            'success': 'green',
            'failed': 'red',
            'running': 'orange',
        }
        return format_html(
            '<span style="color: {};">{}</span>',
            colors.get(obj.status, 'black'),
            obj.get_status_display()
        )
    status_display.short_description = 'Status'

    def duration_display(self, obj):
        if obj.duration_seconds:
            return f"{obj.duration_seconds:.2f}s"
        return '-'
    duration_display.short_description = 'Duration'

    def output_preview(self, obj):
        if obj.error:
            return format_html('<span style="color: red;">{}</span>', obj.error[:100])
        if obj.output:
            return obj.output[:100]
        return '-'
    output_preview.short_description = 'Output'
```

---

## Phase 6: Settings

```python
# settings.py
from django.conf import settings


DEFAULTS = {
    'ENABLE_MIDDLEWARE_TRIGGER': True,
    'CHECK_INTERVAL_SECONDS': 60,  # Check for due tasks every 60 seconds
    'USE_DJANGO_TASKS': True,  # Use django-tasks for async execution
}


def get_setting(name, default=None):
    """Get a sqlery setting.

    Looks in Django settings.DJANGO_SQL_JOBS dict, then DEFAULTS.
    """
    user_settings = getattr(settings, 'DJANGO_SQL_JOBS', {})

    if name in user_settings:
        return user_settings[name]

    if default is not None:
        return default

    return DEFAULTS.get(name)
```

---

## Phase 7: Package Configuration

### pyproject.toml

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "sqlery"
version = "0.1.0"
description = "Cron-based job scheduling for Django without separate processes"
readme = "README.md"
requires-python = ">=3.10"
license = {text = "MIT"}
authors = [
    {name = "Your Name", email = "your.email@example.com"},
]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Framework :: Django",
    "Framework :: Django :: 4.2",
    "Framework :: Django :: 5.0",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
]
dependencies = [
    "django>=4.2",
    "croniter>=2.0.0",
]

[project.optional-dependencies]
tasks = ["django-tasks>=0.1.0"]
dev = [
    "pytest",
    "pytest-django",
    "pytest-cov",
    "black",
    "ruff",
]

[project.urls]
Homepage = "https://github.com/intrepid-g/sqlery"
Repository = "https://github.com/intrepid-g/sqlery"
Issues = "https://github.com/intrepid-g/sqlery/issues"

[tool.hatch.build.targets.wheel]
packages = ["src/sqlery"]

[tool.pytest.ini_options]
DJANGO_SETTINGS_MODULE = "tests.settings"
python_files = ["test_*.py"]
testpaths = ["tests"]

[tool.black]
line-length = 100
target-version = ['py310']

[tool.ruff]
line-length = 100
target-version = "py310"
```

### apps.py

```python
# apps.py
from django.apps import AppConfig


class DjangoSqlJobsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'sqlery'
    verbose_name = 'SQL-Based Job Scheduler'
```

---

## Phase 8: Testing Strategy

### Test Models

```python
# tests/test_models.py
import pytest
from datetime import datetime, timezone
from sqlery.models import ScheduledTask, TaskExecution


@pytest.mark.django_db
def test_scheduled_task_creation():
    """Test creating a scheduled task."""
    task = ScheduledTask.objects.create(
        name="Test Task",
        task_path="tests.tasks.dummy_task",
        cron_expression="0 0 * * *"
    )
    assert task.next_run_at is not None
    assert task.enabled is True


@pytest.mark.django_db
def test_task_execution_lifecycle():
    """Test execution status transitions."""
    task = ScheduledTask.objects.create(
        name="Test",
        task_path="tests.tasks.dummy_task",
        cron_expression="* * * * *"
    )

    execution = TaskExecution.objects.create(task=task)
    assert execution.status == 'running'

    execution.mark_success("Result")
    assert execution.status == 'success'
    assert execution.output == "Result"
    assert execution.finished_at is not None
```

### Test Executor

```python
# tests/test_executor.py
import pytest
from sqlery.executor import TaskExecutor
from sqlery.models import ScheduledTask
from django.utils import timezone


@pytest.mark.django_db
def test_get_due_tasks():
    """Test finding due tasks."""
    executor = TaskExecutor()

    # Create task that's due
    now = timezone.now()
    task1 = ScheduledTask.objects.create(
        name="Due Task",
        task_path="tests.tasks.dummy_task",
        cron_expression="* * * * *",
        next_run_at=now
    )

    # Create task that's not due
    future = now + timezone.timedelta(hours=1)
    task2 = ScheduledTask.objects.create(
        name="Future Task",
        task_path="tests.tasks.dummy_task",
        cron_expression="* * * * *",
        next_run_at=future
    )

    due_tasks = executor.get_due_tasks()
    assert task1 in due_tasks
    assert task2 not in due_tasks


@pytest.mark.django_db
def test_concurrency_check():
    """Test that concurrent executions are prevented."""
    from sqlery.models import TaskExecution

    executor = TaskExecutor()
    task = ScheduledTask.objects.create(
        name="Test",
        task_path="tests.tasks.dummy_task",
        cron_expression="* * * * *"
    )

    # Create running execution
    TaskExecution.objects.create(task=task, status='running')

    # Should not be able to execute
    assert executor.can_execute(task) is False
```

---

## Implementation Checklist

### Week 1: Foundation
- [ ] Set up project structure
- [ ] Create `pyproject.toml`
- [ ] Implement models (`ScheduledTask`, `TaskExecution`)
- [ ] Write initial migration
- [ ] Implement cron utilities (`calculate_next_run`, `validate_cron_expression`)
- [ ] Implement task import utility
- [ ] Write model tests

### Week 2: Execution Engine
- [ ] Implement `TaskExecutor` class
- [ ] Implement `get_due_tasks()`
- [ ] Implement `can_execute()` concurrency check
- [ ] Implement `execute_task()`
- [ ] Implement `run_due_tasks()`
- [ ] Write executor tests
- [ ] Test with real task functions

### Week 3: Triggers & Integration
- [ ] Implement middleware trigger
- [ ] Implement management command
- [ ] Implement trigger helper with django-tasks support
- [ ] Create settings module
- [ ] Write integration tests
- [ ] Test middleware throttling
- [ ] Test both sync and async execution

### Week 4: Admin & Polish
- [ ] Implement `ScheduledTaskAdmin`
- [ ] Implement `TaskExecutionAdmin`
- [ ] Add admin actions (run now, enable/disable)
- [ ] Write README with examples
- [ ] Create demo project
- [ ] Document serverless deployment
- [ ] Final testing and bug fixes

---

## Usage Examples

### Basic Setup

```python
# settings.py
INSTALLED_APPS = [
    'django.contrib.admin',
    # ...
    'sqlery',
]

MIDDLEWARE = [
    # ...
    'sqlery.middleware.ScheduledTaskMiddleware',  # Add this
]

# Optional configuration
DJANGO_SQL_JOBS = {
    'ENABLE_MIDDLEWARE_TRIGGER': True,
    'CHECK_INTERVAL_SECONDS': 60,
    'USE_DJANGO_TASKS': True,
}
```

### Define a Task

```python
# myapp/tasks.py
def send_daily_report():
    """Send daily report at 8 AM."""
    # ... send report
    return "Report sent successfully"
```

### Traditional Deployment

```bash
# Runs via middleware automatically when app receives traffic
python manage.py runserver
```

### Serverless Deployment

```bash
# Add to cron/EventBridge to run every minute
python manage.py run_scheduled_tasks
```

### AWS Lambda Handler

```python
# lambda_handler.py
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

def handler(event, context):
    from django.core.management import call_command
    call_command('run_scheduled_tasks')
    return {'statusCode': 200}
```

---

## Success Criteria

✅ **Installation**: `pip install sqlery` → Add to INSTALLED_APPS → Works
✅ **Admin UI**: Can create/edit/delete tasks via Django Admin
✅ **Execution**: Tasks run at scheduled times without separate processes
✅ **Concurrency**: Multiple executions prevented
✅ **History**: Full execution history visible in admin
✅ **Dual Mode**: Works in both traditional and serverless deployments
✅ **Tests**: >80% code coverage
✅ **Docs**: Clear README with examples

---

## Next Steps After MVP

**Future Enhancements** (post-MVP):
- Task arguments (JSONField for kwargs)
- Retry strategies (beyond django-tasks defaults)
- Task dependencies/chains
- Webhook notifications on failure
- Prometheus metrics export
- Advanced concurrency (Postgres advisory locks)
- Task result storage beyond executions
- Periodic task discovery (auto-register from decorators)

**Focus for MVP: Simple, solid, works everywhere.**
