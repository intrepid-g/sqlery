"""Middleware for direct subprocess spawning (no HTTP layer)."""

import subprocess
import sys
import os
import logging
from pathlib import Path

from django.conf import settings as django_settings
from django.core.cache import cache

from .subprocess_executor import get_manage_py_path
from .settings import get_setting

logger = logging.getLogger(__name__)

TRIGGER_LOG_NAME = "sqlery_subprocess_trigger.log"


def _trigger_log_path() -> Path | None:
    """Where a spawned run_jobs child writes its stdout/stderr, or None if nowhere."""
    configured = get_setting("SUBPROCESS_TRIGGER_LOG", None)
    if configured is not None:
        # Explicit opt-out: SUBPROCESS_TRIGGER_LOG = "" (or False) restores DEVNULL.
        return Path(configured) if configured else None
    base_dir = getattr(django_settings, "BASE_DIR", None)
    if not base_dir:
        return None
    return Path(base_dir) / "tmp" / TRIGGER_LOG_NAME


def _open_trigger_log():
    """Open the trigger log in append mode, falling back to DEVNULL on any failure."""
    path = _trigger_log_path()
    if path is None:
        return subprocess.DEVNULL
    try:
        path.parent.mkdir(exist_ok=True, parents=True)
        return open(path, "a")
    except Exception as e:
        # Never let logging break job execution — this is the only executor there is.
        logger.warning(f"Cannot open sqlery subprocess trigger log {path}: {e}")
        return subprocess.DEVNULL


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

        # Old: stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        # Discarding both streams made this mode undebuggable: a child that died
        # mid-job left the row at status='running' with empty error/traceback and
        # NOTHING anywhere said why. In a web-container-only deployment this is the
        # only executor there is, so its failures must be recoverable from disk.
        child_output = _open_trigger_log()

        # Spawn subprocess (fire-and-forget)
        # NOTE: Each invocation processes EXACTLY ONE job
        subprocess.Popen(
            [
                sys.executable,
                manage_py,
                "run_jobs",
                # No --once flag - command now always processes ONE job
            ],
            stdout=child_output,
            stderr=subprocess.STDOUT,
            env=os.environ,  # Inherit environment (critical!)
            start_new_session=True,  # Detach from parent, prevents zombies
            close_fds=True,  # Close file descriptors
        )
        if child_output not in (subprocess.DEVNULL, None):
            # Parent's copy is no longer needed; the child holds its own fd.
            try:
                child_output.close()
            except Exception:  # pragma: no cover - fd already gone
                pass

        logger.debug(f"Spawned subprocess: {sys.executable} {manage_py} run_jobs")
