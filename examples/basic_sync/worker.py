"""Worker process example.

Run it from this folder: python worker.py
It stops after 3 jobs. Use the sqlery-worker command to run without a limit.
"""

from sqlery import Worker
from sqlery.compat import initialize
import tasks  # noqa: F401  (the worker must be able to import the tasks)


def main():
    """Run a worker that processes the 'default' and 'reports' queues."""
    initialize(database_url="sqlite:///example.db", enable_daemon=False)
    worker = Worker(["default", "reports"])
    print("Processing queues: default, reports (stops after 3 jobs)")
    worker.run(max_jobs=3)


if __name__ == "__main__":
    main()
