"""Poll Redis until all RQ jobs are done, then print benchmark results."""

import os
import statistics
import sys
import time

import redis
from rq import Queue
from rq.job import Job
from rq.registry import FailedJobRegistry, FinishedJobRegistry

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379")
TOTAL_JOBS = int(os.environ.get("TOTAL_JOBS", 500))
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", 2.0))
TIMEOUT = int(os.environ.get("TIMEOUT", 600))


def wait_for_completion(conn: redis.Redis, finished: FinishedJobRegistry, failed: FailedJobRegistry) -> None:
    deadline = time.time() + TIMEOUT
    while True:
        done = len(finished) + len(failed)
        print(f"  [rq] {done}/{TOTAL_JOBS} jobs done...", end="\r", flush=True)
        if done >= TOTAL_JOBS:
            print()
            return
        if time.time() > deadline:
            print(f"\n[rq] Timeout — only {done}/{TOTAL_JOBS} jobs completed")
            sys.exit(1)
        time.sleep(POLL_INTERVAL)


def print_results(conn: redis.Redis, finished: FinishedJobRegistry, failed: FailedJobRegistry) -> None:
    finished_ids = finished.get_job_ids()
    failed_ids = failed.get_job_ids()
    all_ids = finished_ids + failed_ids

    jobs = [j for j in Job.fetch_many(all_ids, connection=conn) if j is not None]

    enqueued_times = [j.enqueued_at for j in jobs if j.enqueued_at]
    ended_times = [j.ended_at for j in jobs if j.ended_at]
    durations = [
        (j.ended_at - j.started_at).total_seconds()
        for j in jobs
        if j.started_at and j.ended_at
    ]

    first_enqueued = min(enqueued_times) if enqueued_times else None
    last_ended = max(ended_times) if ended_times else None
    wall_time = (last_ended - first_enqueued).total_seconds() if first_enqueued and last_ended else 0
    total = len(jobs)
    throughput = total / wall_time if wall_time > 0 else 0
    avg_dur = statistics.mean(durations) * 1000 if durations else 0

    durations_s = sorted(durations)
    p50 = statistics.median(durations_s) * 1000 if durations_s else 0
    p95 = durations_s[max(0, int(len(durations_s) * 0.95) - 1)] * 1000 if durations_s else 0

    print("=" * 52)
    print("  RQ BENCHMARK RESULTS")
    print("=" * 52)
    print(f"  Total jobs   : {total}")
    print(f"  Success      : {len(finished_ids)}")
    print(f"  Failed       : {len(failed_ids)}")
    print(f"  Wall time    : {wall_time:.2f}s")
    print(f"  Throughput   : {throughput:.1f} jobs/sec")
    print(f"  Avg duration : {avg_dur:.0f}ms")
    print(f"  P50 duration : {p50:.0f}ms")
    print(f"  P95 duration : {p95:.0f}ms")
    print("=" * 52)


def main() -> None:
    # Small delay so the producer has time to start enqueueing
    time.sleep(3)

    conn = redis.from_url(REDIS_URL)
    finished = FinishedJobRegistry("stress", connection=conn)
    failed = FailedJobRegistry("stress", connection=conn)

    wait_for_completion(conn, finished, failed)
    print_results(conn, finished, failed)


if __name__ == "__main__":
    main()
