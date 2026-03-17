"""Poll postgres until all jobs are done, then print benchmark results."""

import os
import statistics
import sys
import time

import psycopg

DATABASE_URL = os.environ["DATABASE_URL"]
TOTAL_JOBS = int(os.environ.get("TOTAL_JOBS", 500))
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", 2.0))
TIMEOUT = int(os.environ.get("TIMEOUT", 600))

# psycopg v3 connect needs plain postgresql:// scheme
_db_url = DATABASE_URL.replace("postgresql+psycopg://", "postgresql://")


def wait_for_completion(conn: psycopg.Connection) -> None:
    deadline = time.time() + TIMEOUT
    while True:
        row = conn.execute(
            "SELECT COUNT(*) FROM sqlery_queued_job"
            " WHERE queue_name = 'stress' AND status IN ('success', 'failed')"
        ).fetchone()
        done = row[0]
        print(f"  [sqlery] {done}/{TOTAL_JOBS} jobs done...", end="\r", flush=True)
        if done >= TOTAL_JOBS:
            print()
            return
        if time.time() > deadline:
            print(f"\n[sqlery] Timeout — only {done}/{TOTAL_JOBS} jobs completed")
            sys.exit(1)
        time.sleep(POLL_INTERVAL)


def print_results(conn: psycopg.Connection) -> None:
    row = conn.execute("""
        SELECT
            COUNT(*)                                              AS total,
            COUNT(*) FILTER (WHERE status = 'success')           AS success,
            COUNT(*) FILTER (WHERE status = 'failed')            AS failed,
            MIN(created_at)                                       AS first_created,
            MAX(finished_at)                                      AS last_finished,
            AVG(duration_seconds)                                 AS avg_dur
        FROM sqlery_queued_job
        WHERE queue_name = 'stress'
    """).fetchone()

    total, success, failed, first_created, last_finished, avg_dur = row

    durations = [
        r[0]
        for r in conn.execute(
            "SELECT duration_seconds FROM sqlery_queued_job"
            " WHERE queue_name = 'stress' AND duration_seconds IS NOT NULL"
        ).fetchall()
    ]

    wall_time = (last_finished - first_created).total_seconds() if first_created and last_finished else 0
    throughput = total / wall_time if wall_time > 0 else 0

    durations_s = sorted(durations)
    p50 = statistics.median(durations_s) * 1000 if durations_s else 0
    p95 = durations_s[max(0, int(len(durations_s) * 0.95) - 1)] * 1000 if durations_s else 0

    print("=" * 52)
    print("  SQLERY BENCHMARK RESULTS")
    print("=" * 52)
    print(f"  Total jobs   : {total}")
    print(f"  Success      : {success}")
    print(f"  Failed       : {failed}")
    print(f"  Wall time    : {wall_time:.2f}s")
    print(f"  Throughput   : {throughput:.1f} jobs/sec")
    print(f"  Avg duration : {(avg_dur or 0) * 1000:.0f}ms")
    print(f"  P50 duration : {p50:.0f}ms")
    print(f"  P95 duration : {p95:.0f}ms")
    print("=" * 52)


_MAX_CONNECT_ATTEMPTS = 30
_CONNECT_RETRY_SLEEP = 2


def main() -> None:
    # Retry connecting until postgres + tables are ready (workers create tables on first connect)
    conn = None
    for attempt in range(1, _MAX_CONNECT_ATTEMPTS + 1):
        try:
            conn = psycopg.connect(_db_url)
            # Verify the job table exists before proceeding
            conn.execute("SELECT 1 FROM sqlery_queued_job LIMIT 1")
            break
        except Exception as exc:
            if conn is not None:
                conn.close()
                conn = None
            if attempt == _MAX_CONNECT_ATTEMPTS:
                print(f"[sqlery-reporter] Could not connect after {_MAX_CONNECT_ATTEMPTS} attempts: {exc}")
                sys.exit(1)
            print(f"[sqlery-reporter] Waiting for DB/tables (attempt {attempt}): {exc}")
            time.sleep(_CONNECT_RETRY_SLEEP)

    with conn:
        wait_for_completion(conn)
        print_results(conn)


if __name__ == "__main__":
    main()
