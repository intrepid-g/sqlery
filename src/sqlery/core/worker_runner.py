"""Worker runner script - entry point for spawned worker processes.

This script is called by WorkerPoolManager.spawn_worker() to run a worker
as a separate subprocess. It works in both Django and standalone modes.

The worker runs persistently, polling for jobs until terminated by the daemon.
"""

import sys
import os


def main():
    """Main entry point for worker runner."""
    # # Old: always log to stderr (captured by parent's raw file → grows forever)
    # logging.basicConfig(
    #     level=logging.INFO,
    #     format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    #     handlers=[logging.StreamHandler(sys.stderr)],
    # )

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
    configure_logging(f'sqlery_worker_{os.getpid()}.log')

    # Import and run worker (mode detection will happen correctly now)
    from sqlery.core.worker import WorkerProcess
    from sqlery.compat import get_config

    # Get queue configuration
    queues = get_config('WORKER_QUEUES', ['default'])

    # Create and run persistent worker (blocks until SIGTERM/SIGINT)
    worker = WorkerProcess(queues=queues)
    worker.run()


if __name__ == '__main__':
    main()
