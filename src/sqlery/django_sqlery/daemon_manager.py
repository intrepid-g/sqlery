"""Daemon process management - PID file handling and status checks.

DEPRECATED in v0.11.0: Use sqlery.core.daemon.DaemonManager instead.

This module is kept for backward compatibility and will be removed in v0.12.0.
All functions now redirect to core.daemon.DaemonManager.
"""

import os
import time
import signal
import logging
import warnings
from pathlib import Path
# from typing import Optional  # Replaced with X | None (Python 3.10+)

logger = logging.getLogger(__name__)


def _deprecation_warning(func_name: str):
    """Show deprecation warning for daemon_manager functions."""
    warnings.warn(
        f"sqlery.django_sqlery.daemon_manager.{func_name}() is deprecated. "
        f"Use sqlery.core.daemon.DaemonManager instead. "
        f"This function will be removed in v0.12.0.",
        DeprecationWarning,
        stacklevel=3
    )


def get_pid_file_path() -> Path:
    """Get path to PID file.

    Returns:
        Path to PID file in BASE_DIR/tmp/
    """
    _deprecation_warning('get_pid_file_path')
    from sqlery.core.daemon import DaemonManager
    return DaemonManager().pid_file


def get_heartbeat_file_path() -> Path:
    """Get path to heartbeat file.

    Returns:
        Path to heartbeat file in BASE_DIR/tmp/
    """
    _deprecation_warning('get_heartbeat_file_path')
    from sqlery.core.daemon import DaemonManager
    return DaemonManager().heartbeat_file


def read_pid_file() -> int | None:
    """Read PID from file.

    Returns:
        PID as integer, or None if file doesn't exist or is invalid
    """
    _deprecation_warning('read_pid_file')
    from sqlery.core.daemon import DaemonManager
    return DaemonManager().read_pid()


def is_process_running(pid: int) -> bool:
    """Check if a process with given PID is running.

    Args:
        pid: Process ID to check

    Returns:
        True if process exists, False otherwise
    """
    _deprecation_warning('is_process_running')
    from sqlery.core.daemon import DaemonManager
    return DaemonManager().is_process_running(pid)


def is_daemon_running() -> bool:
    """Check if daemon is currently running.

    Returns:
        True if daemon process is active, False otherwise
    """
    _deprecation_warning('is_daemon_running')
    from sqlery.core.daemon import DaemonManager
    return DaemonManager().is_running()


def get_daemon_status() -> dict:
    """Get detailed daemon status information.

    Returns:
        Dictionary with status information:
        - running: bool
        - pid: int or None
        - heartbeat_age: int (seconds since last heartbeat) or None
        - stale: bool (heartbeat is old)
    """
    _deprecation_warning('get_daemon_status')
    from sqlery.core.daemon import DaemonManager
    return DaemonManager().status()


def stop_daemon(force: bool = False) -> bool:
    """Stop the daemon process.

    Args:
        force: If True, use SIGKILL instead of SIGTERM

    Returns:
        True if daemon was stopped, False if not running
    """
    _deprecation_warning('stop_daemon')
    from sqlery.core.daemon import DaemonManager
    return DaemonManager().stop(force=force)


def cleanup_stale_pid() -> bool:
    """Remove PID file if process is not running.

    Returns:
        True if cleaned up, False if daemon is still running
    """
    _deprecation_warning('cleanup_stale_pid')
    from sqlery.core.daemon import DaemonManager
    return DaemonManager().cleanup_stale()
