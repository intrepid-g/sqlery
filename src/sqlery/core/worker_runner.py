"""Worker runner script - entry point for spawned worker processes.

This script is called by WorkerPoolManager.spawn_worker() to run a worker
as a separate subprocess. It works in both Django and standalone modes.

The worker runs persistently, polling for jobs until terminated by the daemon.
"""

import json
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

        # Setup Django — guarded so the runner is safely importable in
        # standalone mode (Django bootstrap is a no-op when django is absent).
        try:
            import django  # type: ignore[import-not-found]
            django.setup()
        except ImportError:
            # Django bootstrap — no-op in standalone mode
            pass

    from sqlery.compat import initialize, is_standalone_mode

    if is_standalone_mode() and os.environ.get("SQLERY_DATABASE_URL"):
        initialize(database_url=os.environ["SQLERY_DATABASE_URL"], enable_daemon=False)

    run_worker()


def run_worker():
    """Configure logging and run one worker until it is told to stop.

    Called by main() in a fresh interpreter (WORKER_START_METHOD="spawn") and, with Django
    or the database already set up, in a worker forked by the launcher ("fork", #163).
    """
    from sqlery.compat import get_config

    # Configure logging after Django/standalone setup so compat layer works.
    from sqlery.core.log_config import configure_logging
    configure_logging(f'sqlery_worker_{os.getpid()}.log')

    # Import and run worker (mode detection will happen correctly now)
    from sqlery.core.worker import WorkerProcess

    # Get queue configuration
    # The spawning pool passes its queues via env (may differ from settings, e.g. rqworker).
    queues = None
    env_queues = os.environ.get('SQLERY_WORKER_QUEUES')
    if env_queues:
        try:
            parsed = json.loads(env_queues)
            if isinstance(parsed, list) and parsed and all(isinstance(q, str) for q in parsed):
                queues = parsed
        except ValueError:
            pass
    if queues is None:
        queues = get_config('WORKER_QUEUES', ['default'])

    # Create and run persistent worker (blocks until SIGTERM/SIGINT)
    worker = WorkerProcess(queues=queues)
    worker.run()


if __name__ == '__main__':
    main()
