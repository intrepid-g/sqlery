"""Subprocess execution wrappers to prevent memory leaks."""

import subprocess
import sys
import os
import logging
from typing import Literal

logger = logging.getLogger(__name__)


def get_manage_py_path() -> str:
    """Get absolute path to manage.py using Django's BASE_DIR.

    This prevents issues where CWD != project root in production deployments
    (Docker, systemd, supervisor, cloud functions, etc.).

    Returns:
        Absolute path to manage.py

    Raises:
        RuntimeError: If BASE_DIR is not configured or manage.py not found
    """
    try:
        from django.conf import settings

        # Get Django project root
        if not hasattr(settings, 'BASE_DIR'):
            raise RuntimeError(
                "settings.BASE_DIR is not configured. "
                "Sqlery requires BASE_DIR to locate manage.py"
            )

        base_dir = settings.BASE_DIR
        manage_py = os.path.join(base_dir, 'manage.py')

        # Verify file exists
        if not os.path.isfile(manage_py):
            # Try parent directory (common structure: BASE_DIR/project/manage.py)
            parent_manage = os.path.join(os.path.dirname(base_dir), 'manage.py')
            if os.path.isfile(parent_manage):
                return parent_manage

            raise RuntimeError(
                f"manage.py not found at {manage_py}. "
                f"BASE_DIR is set to {base_dir}"
            )

        return manage_py

    except ImportError:
        raise RuntimeError(
            "Django is not configured. "
            "Ensure DJANGO_SETTINGS_MODULE is set correctly."
        )


def run_scheduler_subprocess() -> dict[str, int]:
    """Run scheduler in subprocess to prevent memory leaks.

    Returns:
        dict with 'returncode' and 'jobs_created' keys
    """
    try:
        manage_py = get_manage_py_path()

        result = subprocess.run(
            [
                sys.executable,
                manage_py,
                "run_jobs",
                "--scheduler-only",
                "--once",
            ],
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
        )

        if result.returncode == 0:
            logger.info(f"Scheduler subprocess completed: {result.stdout.strip()}")
        else:
            logger.error(f"Scheduler subprocess failed: {result.stderr.strip()}")

        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    except subprocess.TimeoutExpired:
        logger.error("Scheduler subprocess timed out after 5 minutes")
        return {"returncode": -1, "error": "timeout"}
    except Exception as e:
        logger.error(f"Failed to run scheduler subprocess: {e}")
        return {"returncode": -1, "error": str(e)}


def run_worker_subprocess(queue_name: str | None = None) -> dict[str, int]:
    """Run queue worker in subprocess to prevent memory leaks.

    Args:
        queue_name: Optional queue name to process

    Returns:
        dict with 'returncode', 'stdout', 'stderr' keys
    """
    manage_py = get_manage_py_path()

    cmd = [
        sys.executable,
        manage_py,
        "run_jobs",
        "--worker-only",
        "--once",
    ]

    if queue_name:
        cmd.extend(["--queue", queue_name])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
        )

        if result.returncode == 0:
            logger.info(f"Worker subprocess completed: {result.stdout.strip()}")
        else:
            logger.error(f"Worker subprocess failed: {result.stderr.strip()}")

        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    except subprocess.TimeoutExpired:
        logger.error("Worker subprocess timed out after 5 minutes")
        return {"returncode": -1, "error": "timeout"}
    except Exception as e:
        logger.error(f"Failed to run worker subprocess: {e}")
        return {"returncode": -1, "error": str(e)}


def should_use_subprocess() -> bool:
    """Determine if subprocess mode should be used based on configuration.

    Returns:
        True if subprocess mode should be used
    """
    from .settings import get_setting

    execution_mode = get_setting("EXECUTION_MODE", "auto")

    if execution_mode == "subprocess":
        return True
    elif execution_mode == "thread":
        return False
    elif execution_mode == "django-tasks":
        return False
    elif execution_mode == "auto":
        # Auto mode: prefer django-tasks if available, otherwise subprocess
        try:
            import django_tasks
            return False  # Use django-tasks
        except ImportError:
            return True  # Fallback to subprocess
    else:
        logger.warning(f"Unknown EXECUTION_MODE: {execution_mode}, using auto")
        return True


def get_execution_strategy() -> Literal["subprocess", "django-tasks", "thread"]:
    """Get the execution strategy based on configuration and availability.

    Returns:
        One of: 'subprocess', 'django-tasks', 'thread'
    """
    from .settings import get_setting

    execution_mode = get_setting("EXECUTION_MODE", "auto")

    if execution_mode == "subprocess":
        return "subprocess"
    elif execution_mode == "thread":
        return "thread"
    elif execution_mode == "django-tasks":
        # Check if django-tasks is available
        try:
            import django_tasks
            return "django-tasks"
        except ImportError:
            logger.warning("EXECUTION_MODE is 'django-tasks' but not installed, falling back to subprocess")
            return "subprocess"
    elif execution_mode == "auto":
        # Auto mode: prefer django-tasks if available, otherwise subprocess
        try:
            import django_tasks
            use_django_tasks = get_setting("USE_DJANGO_TASKS", True)
            if use_django_tasks:
                return "django-tasks"
            else:
                return "subprocess"
        except ImportError:
            return "subprocess"
    else:
        logger.warning(f"Unknown EXECUTION_MODE: {execution_mode}, using subprocess")
        return "subprocess"
