"""Shared logging configuration for sqlery subprocesses.

Provides a single source of truth for:
- Debug mode detection (SQLERY_DEBUG env var)
- Log directory resolution (Django vs standalone)
- RotatingFileHandler setup (normal mode) vs StreamHandler (debug mode)

TODO: Decide on the best default logging strategy for non-debug mode.
Options to consider:
  1. RotatingFileHandler (current) — self-managing files, but still writes to
     disk even when nobody is watching. Orphaned .log files accumulate if the
     log dir is never cleaned.
  2. NullHandler — emit nothing by default; let the deployer attach handlers
     via Django LOGGING / dictConfig. Zero surprise files on disk.
  3. StreamHandler to stderr — plays nicely with Docker / systemd / journald
     (they handle rotation). No files to manage, but noisy if running bare.
  4. SysLogHandler / WatchedFileHandler — delegates rotation to the OS
     (logrotate / syslog). More "unix-y" but adds an external dependency on
     the host being configured correctly.
  5. TimedRotatingFileHandler — rotate by time instead of size; avoids large
     files during bursts, but can produce many small files during quiet periods.
"""

import os
import sys
import logging
from pathlib import Path
from logging.handlers import RotatingFileHandler

from sqlery.compat import get_config, is_django_mode

try:
    from django.conf import settings as django_settings
except ImportError:
    django_settings = None

LOG_FORMAT = '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
LOG_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
LOG_BACKUP_COUNT = 3


def is_debug_mode() -> bool:
    """Check if SQLERY_DEBUG env var is set."""
    return os.environ.get('SQLERY_DEBUG', '').lower() in ('1', 'true', 'yes')


def get_log_dir() -> Path:
    """Get log directory based on mode (Django vs standalone).

    Must be called after Django setup if in Django mode.
    """
    if is_django_mode():
        # from django.conf import settings  # moved to top-level (try/except)
        log_dir = Path(django_settings.BASE_DIR) / 'tmp'
    else:
        log_dir = Path(get_config('LOG_DIR', '/tmp/sqlery'))
    log_dir.mkdir(exist_ok=True, parents=True)
    return log_dir


def configure_logging(log_filename: str, debug_stream=None):
    """Configure logging for a subprocess entry point.

    Normal mode: RotatingFileHandler writing to {log_dir}/{log_filename}.
    Debug mode (SQLERY_DEBUG=1): StreamHandler to debug_stream at DEBUG level.

    Must be called after Django setup if in Django mode.

    Args:
        log_filename: Name of the log file (e.g. 'sqlery_daemon.log').
        debug_stream: Stream for debug mode output (default: sys.stderr).
    """
    if debug_stream is None:
        debug_stream = sys.stderr

    if is_debug_mode():
        logging.basicConfig(
            level=logging.DEBUG,
            format=LOG_FORMAT,
            handlers=[logging.StreamHandler(debug_stream)],
        )
    else:
        log_file = get_log_dir() / log_filename
        handler = RotatingFileHandler(
            log_file,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
        )
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        logging.basicConfig(
            level=logging.INFO,
            handlers=[handler],
        )
