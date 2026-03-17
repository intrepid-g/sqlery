"""Tests for subprocess execution mode."""

import pytest
import os
import tempfile
from unittest.mock import patch, MagicMock
from sqlery.subprocess_executor import (
    get_execution_strategy,
    should_use_subprocess,
    run_scheduler_subprocess,
    run_worker_subprocess,
    get_manage_py_path,
)


class TestManagePyPathResolution:
    """Test absolute path resolution for manage.py."""

    def test_get_manage_py_path_success(self, settings):
        """get_manage_py_path should return absolute path when manage.py exists."""
        # Create a temporary directory with manage.py
        with tempfile.TemporaryDirectory() as tmpdir:
            manage_py_path = os.path.join(tmpdir, 'manage.py')

            # Create a dummy manage.py file
            with open(manage_py_path, 'w') as f:
                f.write('#!/usr/bin/env python\n')

            # Mock settings.BASE_DIR to point to our temp directory
            settings.BASE_DIR = tmpdir

            result = get_manage_py_path()

            assert result == manage_py_path
            assert os.path.isabs(result)
            assert os.path.exists(result)

    def test_get_manage_py_path_parent_directory(self, settings):
        """get_manage_py_path should check parent directory if not found in BASE_DIR."""
        # Create a temporary directory structure: parent/child/
        with tempfile.TemporaryDirectory() as parent_dir:
            child_dir = os.path.join(parent_dir, 'child')
            os.makedirs(child_dir)

            # Put manage.py in parent directory
            manage_py_path = os.path.join(parent_dir, 'manage.py')
            with open(manage_py_path, 'w') as f:
                f.write('#!/usr/bin/env python\n')

            # Set BASE_DIR to child directory (manage.py not here)
            settings.BASE_DIR = child_dir

            result = get_manage_py_path()

            assert result == manage_py_path
            assert os.path.isabs(result)

    def test_get_manage_py_path_not_found(self, settings):
        """get_manage_py_path should raise RuntimeError if manage.py not found."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Directory exists but no manage.py file
            settings.BASE_DIR = tmpdir

            with pytest.raises(RuntimeError, match="manage.py not found"):
                get_manage_py_path()

    def test_get_manage_py_path_no_base_dir(self, settings):
        """get_manage_py_path should raise RuntimeError if BASE_DIR not configured."""
        # Remove BASE_DIR attribute
        if hasattr(settings, 'BASE_DIR'):
            delattr(settings, 'BASE_DIR')

        with pytest.raises(RuntimeError, match="BASE_DIR is not configured"):
            get_manage_py_path()

    def test_subprocess_uses_absolute_path(self, settings):
        """Subprocess commands should use absolute path to manage.py."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manage_py_path = os.path.join(tmpdir, 'manage.py')
            with open(manage_py_path, 'w') as f:
                f.write('#!/usr/bin/env python\n')

            settings.BASE_DIR = tmpdir

            with patch("sqlery.django_sqlery.subprocess_executor.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout="", stderr=""
                )

                run_scheduler_subprocess()

                # Verify absolute path was used
                call_args = mock_run.call_args[0][0]
                assert manage_py_path in call_args
                assert os.path.isabs(call_args[1])  # Second arg should be manage.py path


class TestExecutionStrategy:
    """Test execution strategy selection."""

    def test_subprocess_mode_explicit(self, settings):
        """EXECUTION_MODE='subprocess' should return subprocess strategy."""
        settings.DJANGO_SQL_JOBS = {"EXECUTION_MODE": "subprocess"}
        assert get_execution_strategy() == "subprocess"

    def test_thread_mode_explicit(self, settings):
        """EXECUTION_MODE='thread' should return thread strategy."""
        settings.DJANGO_SQL_JOBS = {"EXECUTION_MODE": "thread"}
        assert get_execution_strategy() == "thread"

    def test_django_tasks_mode_explicit(self, settings):
        """EXECUTION_MODE='django-tasks' should return django-tasks if available."""
        settings.DJANGO_SQL_JOBS = {"EXECUTION_MODE": "django-tasks"}
        # django_tasks is likely not installed, so it should fallback to subprocess
        # or return django-tasks if installed
        result = get_execution_strategy()
        assert result in ["django-tasks", "subprocess"]

    def test_django_tasks_mode_fallback(self, settings):
        """EXECUTION_MODE='django-tasks' should fallback to subprocess if unavailable."""
        settings.DJANGO_SQL_JOBS = {"EXECUTION_MODE": "django-tasks"}
        # django_tasks not available (ImportError will be raised)
        assert get_execution_strategy() == "subprocess"

    def test_auto_mode_with_django_tasks(self, settings):
        """Auto mode should prefer django-tasks if USE_DJANGO_TASKS=True."""
        settings.DJANGO_SQL_JOBS = {
            "EXECUTION_MODE": "auto",
            "USE_DJANGO_TASKS": True,
        }
        # Will be either django-tasks or subprocess depending on if django_tasks is installed
        strategy = get_execution_strategy()
        assert strategy in ["django-tasks", "subprocess"]

    def test_auto_mode_without_django_tasks(self, settings):
        """Auto mode should use subprocess if django-tasks unavailable."""
        settings.DJANGO_SQL_JOBS = {"EXECUTION_MODE": "auto"}
        # django_tasks not available
        assert get_execution_strategy() == "subprocess"

    def test_invalid_mode_fallback(self, settings):
        """Invalid EXECUTION_MODE should fallback to subprocess."""
        settings.DJANGO_SQL_JOBS = {"EXECUTION_MODE": "invalid"}
        assert get_execution_strategy() == "subprocess"


class TestSubprocessExecution:
    """Test subprocess execution wrappers."""

    @patch("sqlery.django_sqlery.subprocess_executor.subprocess.run")
    @patch("sqlery.django_sqlery.subprocess_executor.get_manage_py_path")
    def test_scheduler_subprocess_success(self, mock_path, mock_run):
        """Scheduler subprocess should execute successfully."""
        mock_path.return_value = "/path/to/manage.py"
        mock_run.return_value = MagicMock(
            returncode=0, stdout="Enqueued 5 jobs", stderr=""
        )

        result = run_scheduler_subprocess()

        assert result["returncode"] == 0
        assert "Enqueued 5 jobs" in result["stdout"]
        mock_run.assert_called_once()

        # Verify command includes manage.py path, run_jobs, --scheduler-only, --once
        call_args = mock_run.call_args[0][0]
        assert "/path/to/manage.py" in call_args
        assert "run_jobs" in call_args
        assert "--scheduler-only" in call_args
        assert "--once" in call_args

    @patch("sqlery.django_sqlery.subprocess_executor.subprocess.run")
    @patch("sqlery.django_sqlery.subprocess_executor.get_manage_py_path")
    def test_scheduler_subprocess_failure(self, mock_path, mock_run):
        """Scheduler subprocess should handle failures gracefully."""
        mock_path.return_value = "/path/to/manage.py"
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="Error occurred"
        )

        result = run_scheduler_subprocess()

        assert result["returncode"] == 1
        assert "Error occurred" in result["stderr"]

    @patch("sqlery.django_sqlery.subprocess_executor.subprocess.run")
    @patch("sqlery.django_sqlery.subprocess_executor.get_manage_py_path")
    def test_worker_subprocess_success(self, mock_path, mock_run):
        """Worker subprocess should execute successfully."""
        mock_path.return_value = "/path/to/manage.py"
        mock_run.return_value = MagicMock(
            returncode=0, stdout="Processed 10 jobs", stderr=""
        )

        result = run_worker_subprocess()

        assert result["returncode"] == 0
        assert "Processed 10 jobs" in result["stdout"]

        # Verify command includes manage.py path, run_jobs, --worker-only, --once
        call_args = mock_run.call_args[0][0]
        assert "/path/to/manage.py" in call_args
        assert "run_jobs" in call_args
        assert "--worker-only" in call_args
        assert "--once" in call_args

    @patch("sqlery.django_sqlery.subprocess_executor.subprocess.run")
    @patch("sqlery.django_sqlery.subprocess_executor.get_manage_py_path")
    def test_worker_subprocess_with_queue(self, mock_path, mock_run):
        """Worker subprocess should support queue filtering."""
        mock_path.return_value = "/path/to/manage.py"
        mock_run.return_value = MagicMock(
            returncode=0, stdout="Processed 3 jobs from email queue", stderr=""
        )

        result = run_worker_subprocess(queue_name="email")

        assert result["returncode"] == 0

        # Verify command includes --queue email
        call_args = mock_run.call_args[0][0]
        assert "--queue" in call_args
        assert "email" in call_args

    @patch("sqlery.django_sqlery.subprocess_executor.subprocess.run")
    @patch("sqlery.django_sqlery.subprocess_executor.get_manage_py_path")
    def test_subprocess_timeout_handling(self, mock_path, mock_run):
        """Subprocess should handle timeouts gracefully."""
        from subprocess import TimeoutExpired

        mock_path.return_value = "/path/to/manage.py"
        mock_run.side_effect = TimeoutExpired("manage.py", 300)

        result = run_scheduler_subprocess()

        assert result["returncode"] == -1
        assert result["error"] == "timeout"

    @patch("sqlery.django_sqlery.subprocess_executor.subprocess.run")
    @patch("sqlery.django_sqlery.subprocess_executor.get_manage_py_path")
    def test_subprocess_exception_handling(self, mock_path, mock_run):
        """Subprocess should handle general exceptions."""
        mock_path.return_value = "/path/to/manage.py"
        mock_run.side_effect = Exception("Unexpected error")

        result = run_scheduler_subprocess()

        assert result["returncode"] == -1
        assert "Unexpected error" in result["error"]


class TestTriggerIntegration:
    """Test trigger functions with different execution modes."""

    @patch("sqlery.triggers.get_execution_strategy")
    @patch("sqlery.triggers.run_scheduler_subprocess")
    def test_trigger_uses_subprocess_mode(self, mock_subprocess, mock_strategy):
        """Trigger should use subprocess when strategy is 'subprocess'."""
        from sqlery.triggers import trigger_due_tasks

        mock_strategy.return_value = "subprocess"
        mock_subprocess.return_value = {"returncode": 0, "stdout": "", "stderr": ""}

        trigger_due_tasks()

        mock_subprocess.assert_called_once()

    @patch("sqlery.triggers.get_execution_strategy")
    @patch("sqlery.triggers._enqueue_synchronously")
    def test_trigger_uses_thread_mode(self, mock_sync, mock_strategy):
        """Trigger should use synchronous execution when strategy is 'thread'."""
        from sqlery.triggers import trigger_due_tasks

        mock_strategy.return_value = "thread"

        trigger_due_tasks()

        mock_sync.assert_called_once()

    @patch("sqlery.triggers.get_execution_strategy")
    @patch("sqlery.triggers.run_worker_subprocess")
    def test_worker_trigger_uses_subprocess_mode(self, mock_subprocess, mock_strategy):
        """Worker trigger should use subprocess when strategy is 'subprocess'."""
        from sqlery.triggers import trigger_queue_workers

        mock_strategy.return_value = "subprocess"
        mock_subprocess.return_value = {"returncode": 0, "stdout": "", "stderr": ""}

        trigger_queue_workers(queue_name="email")

        mock_subprocess.assert_called_once_with("email")
