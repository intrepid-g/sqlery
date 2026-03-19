"""Middleware for direct subprocess spawning (no HTTP layer)."""

import subprocess
import sys
import os
import logging
from django.core.cache import cache

from .subprocess_executor import get_manage_py_path
from .settings import get_setting

logger = logging.getLogger(__name__)


class SubprocessTriggerMiddleware:
    """Middleware that spawns subprocesses directly for job processing.

    Advantages over HTTP trigger mode:
    - No HTTP request to self (simpler, more reliable)
    - No network dependencies
    - No port conflicts or SSL issues
    - Works in all deployment scenarios
    - Fire-and-forget subprocess execution

    Usage:
        MIDDLEWARE = [
            'sqlery.subprocess_middleware.SubprocessTriggerMiddleware',
        ]

        DJANGO_SQL_JOBS = {
            'TRIGGER_MODE': 'subprocess',
        }
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Process request first
        response = self.get_response(request)

        # Spawn subprocess after response (post-response hook)
        self.maybe_spawn_subprocess()

        return response

    def maybe_spawn_subprocess(self):
        """Check if it's time to spawn subprocess for job processing."""
        # from .settings import get_setting  # moved to top-level

        # Check if enabled
        if not get_setting("ENABLE_MIDDLEWARE_TRIGGER", True):
            return

        # Check TRIGGER_MODE
        trigger_mode = get_setting("TRIGGER_MODE", "middleware")
        if trigger_mode != "subprocess":
            return

        # Throttle checks (don't spawn on every request)
        check_interval = get_setting("CHECK_INTERVAL_SECONDS", 60)
        cache_key = "sqlery:last_subprocess_trigger"

        if cache.get(cache_key):
            return  # Already triggered recently

        # Set cache for next interval
        cache.set(cache_key, True, check_interval)

        # Spawn subprocess (fire-and-forget)
        try:
            self.spawn_worker_subprocess()
            logger.info("Spawned worker subprocess for job processing")
        except Exception as e:
            logger.error(f"Failed to spawn worker subprocess: {e}")

    def spawn_worker_subprocess(self):
        """Spawn subprocess to run scheduler and process ONE job.

        Each subprocess processes exactly ONE job then exits (memory leak prevention).
        This method is called periodically, so if jobs remain after a worker exits or
        fails, another worker will be spawned on the next interval.

        Uses subprocess.Popen for fire-and-forget execution.
        Process runs detached to prevent zombies.
        """
        # from .subprocess_executor import get_manage_py_path  # moved to top-level

        # Get absolute path to manage.py (prevents CWD issues)
        manage_py = get_manage_py_path()

        # Spawn subprocess (fire-and-forget)
        # NOTE: Each invocation processes EXACTLY ONE job
        subprocess.Popen(
            [
                sys.executable,
                manage_py,
                "run_jobs",
                # No --once flag - command now always processes ONE job
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=os.environ,  # Inherit environment (critical!)
            start_new_session=True,  # Detach from parent, prevents zombies
            close_fds=True,  # Close file descriptors
        )

        logger.debug(f"Spawned subprocess: {sys.executable} {manage_py} run_jobs")
