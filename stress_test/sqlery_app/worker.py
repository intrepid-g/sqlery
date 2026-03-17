"""Sqlery worker — initializes the backend then polls for jobs continuously."""

import os
import time

from sqlery import Worker
from sqlery.compat import initialize, get_backend

import sqlery_app.tasks  # noqa: F401 — registers tasks before worker starts

DATABASE_URL = os.environ["DATABASE_URL"]

_MAX_ATTEMPTS = 30
_RETRY_SLEEP = 2


def main() -> None:
    # Retry until postgres is ready and tables are created.
    # Multiple workers may race on create_all(); we retry on any error
    # (SQLAlchemy's create_all uses checkfirst=True but a concurrent DDL
    # transaction can still raise UniqueViolation before the check completes).
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            initialize(database_url=DATABASE_URL, max_workers=0, enable_daemon=False)
            break
        except Exception as exc:
            if attempt == _MAX_ATTEMPTS:
                raise
            print(f"[sqlery-worker] init attempt {attempt}/{_MAX_ATTEMPTS} failed: {exc!r} — retrying in {_RETRY_SLEEP}s")
            time.sleep(_RETRY_SLEEP)

    backend = get_backend()
    worker = Worker(["stress"], backend=backend)
    print("[sqlery-worker] Ready — polling for jobs")

    # WorkerProcess.run() processes all available jobs then returns.
    # Loop so the worker keeps picking up new jobs as they arrive.
    while True:
        worker.run()
        time.sleep(0.2)


if __name__ == "__main__":
    main()
