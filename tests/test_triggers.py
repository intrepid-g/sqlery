"""Tests for triggers module (all execution modes).

FAILING TESTS EXPLANATION:
1. test_process_queue_synchronously_with_no_queue_name:
   Error: job2.status == 'queued' instead of 'success'

   Root cause: The `_process_queue_synchronously` function (via TaskExecutor.run_queue_workers)
   processes ONE job then tries to spawn a subprocess for the next job. In the test
   environment, there's no manage.py file, so the subprocess spawn fails silently:

   "Failed to spawn next worker: manage.py not found at /Users/.../manage.py"

   The feature branch's run_queue_workers() by default only processes one job and
   then spawns a new worker process. When once=True is NOT passed, it processes
   one job and exits after failing to spawn the next worker.

   The test expects both jobs to be processed, but only job1 gets processed because
   job2 is left for the (failed) subprocess spawn.

To fix: Either:
- Pass once=True to run_queue_workers() to process all jobs in a loop
- Mock the subprocess spawning in the test
- Set up a proper manage.py for test environment
"""

import pytest
from unittest.mock import patch, MagicMock


class TestTriggerDueTasks:
    """Test trigger_due_tasks with different execution modes."""

    @patch("sqlery.triggers._enqueue_subprocess")
    @patch("sqlery.triggers.get_execution_strategy", return_value="subprocess")
    def test_trigger_uses_subprocess_when_strategy_is_subprocess(
        self, mock_strategy, mock_subprocess
    ):
        """Should use subprocess mode when strategy returns 'subprocess'."""
        from sqlery.triggers import trigger_due_tasks
        trigger_due_tasks()
        # Verify subprocess handler was called with no arguments
        mock_subprocess.assert_called_once_with()

    @patch("sqlery.triggers._enqueue_django_tasks")
    @patch("sqlery.triggers.get_execution_strategy", return_value="django-tasks")
    def test_trigger_uses_django_tasks_when_strategy_is_django_tasks(
        self, mock_strategy, mock_django_tasks
    ):
        """Should use django-tasks when strategy returns 'django-tasks'."""
        from sqlery.triggers import trigger_due_tasks
        trigger_due_tasks()
        # Verify django-tasks handler was called with no arguments
        mock_django_tasks.assert_called_once_with()

    @patch("sqlery.triggers._enqueue_synchronously")
    @patch("sqlery.triggers.get_execution_strategy", return_value="thread")
    def test_trigger_uses_synchronous_when_strategy_is_thread(
        self, mock_strategy, mock_sync
    ):
        """Should use synchronous execution when strategy returns 'thread'."""
        from sqlery.triggers import trigger_due_tasks
        trigger_due_tasks()
        # Verify synchronous handler was called with no arguments
        mock_sync.assert_called_once_with()


class TestTriggerQueueWorkers:
    """Test trigger_queue_workers with different execution modes."""

    @patch("sqlery.triggers._process_queue_subprocess")
    @patch("sqlery.triggers.get_execution_strategy", return_value="subprocess")
    def test_trigger_uses_subprocess_when_strategy_is_subprocess(
        self, mock_strategy, mock_subprocess
    ):
        """Should use subprocess mode when strategy returns 'subprocess'."""
        from sqlery.triggers import trigger_queue_workers
        trigger_queue_workers(queue_name="email")
        mock_subprocess.assert_called_once_with("email")

    @patch("sqlery.triggers._process_queue_django_tasks")
    @patch("sqlery.triggers.get_execution_strategy", return_value="django-tasks")
    def test_trigger_uses_django_tasks_when_strategy_is_django_tasks(
        self, mock_strategy, mock_django_tasks
    ):
        """Should use django-tasks when strategy returns 'django-tasks'."""
        from sqlery.triggers import trigger_queue_workers
        trigger_queue_workers(queue_name="email")
        mock_django_tasks.assert_called_once_with("email")

    @patch("sqlery.triggers._process_queue_synchronously")
    @patch("sqlery.triggers.get_execution_strategy", return_value="thread")
    def test_trigger_uses_synchronous_when_strategy_is_thread(
        self, mock_strategy, mock_sync
    ):
        """Should use synchronous execution when strategy returns 'thread'."""
        from sqlery.triggers import trigger_queue_workers
        trigger_queue_workers(queue_name="email")
        mock_sync.assert_called_once_with("email")


class TestSubprocessMode:
    """Test subprocess execution mode."""

    @patch("sqlery.triggers.run_scheduler_subprocess")
    def test_enqueue_subprocess_calls_subprocess_runner(self, mock_run):
        """Should call run_scheduler_subprocess."""
        mock_run.return_value = {"returncode": 0, "stdout": "", "stderr": ""}
        from sqlery.triggers import _enqueue_subprocess
        _enqueue_subprocess()
        mock_run.assert_called_once()

    @patch("sqlery.triggers.run_scheduler_subprocess")
    def test_enqueue_subprocess_logs_error_on_failure(self, mock_run):
        """Should log error when subprocess fails."""
        mock_run.return_value = {"returncode": 1, "stderr": "Test error"}
        from sqlery.triggers import _enqueue_subprocess
        # Should not raise exception
        _enqueue_subprocess()

    @patch("sqlery.triggers.run_worker_subprocess")
    def test_process_queue_subprocess_calls_subprocess_runner(self, mock_run):
        """Should call run_worker_subprocess with queue name."""
        mock_run.return_value = {"returncode": 0, "stdout": "", "stderr": ""}
        from sqlery.triggers import _process_queue_subprocess
        _process_queue_subprocess(queue_name="email")
        mock_run.assert_called_once_with("email")

    @patch("sqlery.triggers.run_worker_subprocess")
    def test_process_queue_subprocess_logs_error_on_failure(self, mock_run):
        """Should log error when subprocess fails."""
        mock_run.return_value = {"returncode": 1, "stderr": "Test error"}
        from sqlery.triggers import _process_queue_subprocess
        # Should not raise exception
        _process_queue_subprocess()


try:
    import django_tasks
    HAS_DJANGO_TASKS = True
except ImportError:
    HAS_DJANGO_TASKS = False


class TestDjangoTasksMode:
    """Test django-tasks execution mode."""

    @pytest.mark.skipif(not HAS_DJANGO_TASKS, reason="django-tasks not installed")
    @patch("django_tasks.task")
    def test_enqueue_django_tasks_uses_task_decorator(self, mock_task):
        """Should use @task decorator from django-tasks."""
        mock_task_func = MagicMock()
        mock_task.return_value = lambda f: mock_task_func
        from sqlery.triggers import _enqueue_django_tasks
        _enqueue_django_tasks()
        mock_task.assert_called_once()

    @patch("sqlery.triggers._enqueue_synchronously")
    def test_enqueue_django_tasks_fallsback_on_import_error(self, mock_sync):
        """Should fallback to synchronous if django-tasks not available."""
        from sqlery.triggers import _enqueue_django_tasks
        # The function should fallback since django_tasks is not installed
        _enqueue_django_tasks()
        mock_sync.assert_called_once()

    @pytest.mark.skipif(not HAS_DJANGO_TASKS, reason="django-tasks not installed")
    @patch("django_tasks.task")
    def test_process_queue_django_tasks_uses_task_decorator(self, mock_task):
        """Should use @task decorator from django-tasks."""
        mock_task_func = MagicMock()
        mock_task.return_value = lambda f: mock_task_func
        from sqlery.triggers import _process_queue_django_tasks
        _process_queue_django_tasks(queue_name="email")
        mock_task.assert_called_once()

    @patch("sqlery.triggers._process_queue_synchronously")
    def test_process_queue_django_tasks_fallsback_on_import_error(self, mock_sync):
        """Should fallback to synchronous if django-tasks not available."""
        from sqlery.triggers import _process_queue_django_tasks
        # The function should fallback since django_tasks is not installed
        _process_queue_django_tasks(queue_name="email")
        mock_sync.assert_called_once_with("email")


@pytest.mark.django_db
class TestSynchronousMode:
    """Test synchronous execution mode."""

    def test_process_queue_synchronously_runs_executor(self):
        """Should run TaskExecutor.run_queue_workers synchronously."""
        from sqlery.models import QueuedJob
        from sqlery.triggers import _process_queue_synchronously

        job = QueuedJob.objects.create(
            task_path="tests.test_executor.dummy_task",
            queue_name="test",
        )

        _process_queue_synchronously(queue_name="test")

        # Job should be processed
        job.refresh_from_db()
        assert job.status == "success"

    def test_process_queue_synchronously_with_no_queue_name(self):
        """Should process jobs when queue_name is None.

        Note: run_queue_workers() without once=True processes one job then
        spawns a subprocess for the next. Each call processes exactly one job.
        """
        from sqlery.models import QueuedJob
        from sqlery.triggers import _process_queue_synchronously

        job1 = QueuedJob.objects.create(
            task_path="tests.test_executor.dummy_task",
            queue_name="email",
        )
        job2 = QueuedJob.objects.create(
            task_path="tests.test_executor.dummy_task",
            queue_name="reports",
        )

        # Each call processes one job (default mode processes one then spawns subprocess)
        _process_queue_synchronously(queue_name=None)
        _process_queue_synchronously(queue_name=None)

        # Both jobs should be processed
        job1.refresh_from_db()
        job2.refresh_from_db()
        assert job1.status == "success"
        assert job2.status == "success"


class TestErrorHandling:
    """Test error handling in triggers."""

    @patch("sqlery.triggers.run_scheduler_subprocess")
    def test_subprocess_error_is_logged_not_raised(self, mock_run):
        """Subprocess errors should be logged but not raise."""
        mock_run.side_effect = Exception("Subprocess failed")
        from sqlery.triggers import _enqueue_subprocess

        # Should not raise
        try:
            _enqueue_subprocess()
        except Exception:
            pytest.fail("Should not raise exception")

    @patch("sqlery.executor.TaskExecutor")
    def test_synchronous_error_is_raised(self, mock_executor):
        """Synchronous execution errors should propagate."""
        mock_executor.return_value.run_due_tasks.side_effect = Exception("Test error")
        from sqlery.triggers import _enqueue_synchronously

        with pytest.raises(Exception, match="Test error"):
            _enqueue_synchronously()
