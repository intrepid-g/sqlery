"""Daemon runner script - entry point for spawned daemon process.

This script is called by DaemonManager.spawn_daemon() to run the daemon
as a separate subprocess. It works in both Django and standalone modes.
"""

import sys
import os


def main():
    """Main entry point for daemon runner."""
    # Check if Django settings module is set in environment
    # If so, initialize Django BEFORE any sqlery imports
    if 'DJANGO_SETTINGS_MODULE' in os.environ:
        # Django mode - ensure current directory is in sys.path for Django app imports
        if os.getcwd() not in sys.path:
            sys.path.insert(0, os.getcwd())

        # Setup Django
        import django
        django.setup()

    # Configure logging after Django setup so compat layer works
    # (sqlery imports trigger sqlery/__init__.py which needs Django first)
    from sqlery.core.log_config import configure_logging
    configure_logging('sqlery_daemon.log')

    # Import and run daemon (mode detection will happen correctly now)
    from sqlery.core.daemon import DaemonManager

    daemon = DaemonManager()

    # Write PID file (daemon takes over PID management)
    daemon.write_pid(os.getpid())

    # Run daemon main loop
    daemon._run_daemon()


if __name__ == '__main__':
    main()
