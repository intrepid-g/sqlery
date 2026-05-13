"""Middleware for spawning and managing background daemon worker.

SIMPLIFIED in v0.11.0: Now uses core.daemon.DaemonManager for spawning.
All daemon logic is in sqlery.core - this is just a Django adapter.
"""

import logging
from pathlib import Path

from sqlery.core.daemon import DaemonManager

from .settings import get_setting

logger = logging.getLogger(__name__)


class DaemonMiddleware:
    """Middleware that ensures daemon worker is running.

    On the first request, spawns a long-running daemon process using the
    framework-agnostic core.daemon.DaemonManager. The daemon runs independently
    of HTTP traffic.

    This is a thin Django adapter - all daemon logic is in sqlery.core.

    Usage:
        MIDDLEWARE = [
            'sqlery.django_sqlery.daemon_middleware.DaemonMiddleware',
        ]

        SQLERY = {
            'TRIGGER_MODE': 'daemon',
            'DAEMON_CHECK_INTERVAL': 10,  # Check every 10 seconds
        }
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self._daemon_checked = False

    def __call__(self, request):
        # Ensure daemon is running (only check once per middleware instance)
        if not self._daemon_checked:
            self.ensure_daemon_running()
            self._daemon_checked = True

        response = self.get_response(request)
        return response

    def ensure_daemon_running(self):
        """Check if daemon is running, start if not."""
        # from .settings import get_setting  # moved to top-level

        # Check TRIGGER_MODE
        trigger_mode = get_setting('TRIGGER_MODE', 'middleware')
        if trigger_mode != 'daemon':
            return

        # Use core DaemonManager
        # from sqlery.core.daemon import DaemonManager  # moved to top-level

        daemon = DaemonManager()

        # Check if already running
        if daemon.is_running():
            logger.debug("Daemon already running")
            return

        # Spawn daemon using core daemon_runner
        try:
            process = daemon.spawn_daemon()
            if process:
                logger.info(f"Spawned daemon successfully (PID: {process.pid})")
        except Exception as e:
            logger.error(f"Failed to spawn daemon: {e}", exc_info=True)
