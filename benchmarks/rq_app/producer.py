"""Enqueue N benchmark jobs into RQ."""

import os
import time

import redis
from rq import Queue

from rq_app.tasks import increment_and_wait

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379")
TOTAL_JOBS = int(os.environ.get("TOTAL_JOBS", 500))


def main() -> None:
    conn = redis.from_url(REDIS_URL)
    q = Queue("stress", connection=conn)

    print(f"[rq] Enqueueing {TOTAL_JOBS} jobs...")
    start = time.perf_counter()

    for i in range(TOTAL_JOBS):
        q.enqueue(increment_and_wait, job_number=i)

    elapsed = time.perf_counter() - start
    rate = TOTAL_JOBS / elapsed if elapsed > 0 else float("inf")
    print(f"[rq] Enqueued {TOTAL_JOBS} jobs in {elapsed:.2f}s ({rate:.0f} jobs/sec)")


if __name__ == "__main__":
    main()
