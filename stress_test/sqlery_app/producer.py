"""Enqueue N benchmark jobs into sqlery."""

import os
import time

from sqlery.compat import initialize

from sqlery_app.tasks import increment_and_wait  # registers the task

DATABASE_URL = os.environ["DATABASE_URL"]
TOTAL_JOBS = int(os.environ.get("TOTAL_JOBS", 500))

_MAX_INIT_ATTEMPTS = 30
_INIT_RETRY_SLEEP = 3


def main() -> None:
    for attempt in range(1, _MAX_INIT_ATTEMPTS + 1):
        try:
            initialize(database_url=DATABASE_URL, max_workers=0, enable_daemon=False)
            break
        except Exception as exc:
            if attempt == _MAX_INIT_ATTEMPTS:
                raise
            print(f"[sqlery-producer] initialize() failed (attempt {attempt}/{_MAX_INIT_ATTEMPTS}): {exc}. Retrying in {_INIT_RETRY_SLEEP}s...")
            time.sleep(_INIT_RETRY_SLEEP)

    print(f"[sqlery] Enqueueing {TOTAL_JOBS} jobs...")
    start = time.perf_counter()

    for i in range(TOTAL_JOBS):
        increment_and_wait.delay(job_number=i)

    elapsed = time.perf_counter() - start
    rate = TOTAL_JOBS / elapsed if elapsed > 0 else float("inf")
    print(f"[sqlery] Enqueued {TOTAL_JOBS} jobs in {elapsed:.2f}s ({rate:.0f} jobs/sec)")


if __name__ == "__main__":
    main()
