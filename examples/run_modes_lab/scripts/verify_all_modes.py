#!/usr/bin/env python3
"""Cross-mode verification script for the run_modes_lab.

This script enqueues one job per execution mode and verifies that all modes
successfully executed the job by querying the shared sqlery_queued_job table.

Modes tested:
1. daemon_queue - Daemon execution mode
2. subprocess_queue - Subprocess spawning mode
3. thread_queue - Synchronous in-process thread mode
4. http_queue - HTTP trigger mode
5. lambda_queue - Lambda/serverless simulation mode
6. async_queue - Async worker mode (django-tasks)
7. standalone_queue - Standalone FastAPI/SQLModel mode

Exit code:
  0 - All modes passed
  1 - Any mode failed or timeout
"""

import os
import sys
import time
import logging
import psycopg

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment configuration
DATABASE_URL = os.getenv("DATABASE_URL")
STANDALONE_DB_URL = os.getenv("LAB_STANDALONE_DB_URL")
VERIFY_TIMEOUT_SECONDS = int(os.getenv("LAB_VERIFY_TIMEOUT_SECONDS", "60"))

# Queue names for each mode (must match PLAN.md contract and compose.yml env vars)
QUEUES = [
    "daemon_queue",
    "subprocess_queue",
    "thread_queue",
    "http_queue",
    "lambda_queue",
    "async_queue",
    "standalone_queue",
]


def setup_django():
    """Initialize Django before using the ORM or models."""
    import django
    from django.conf import settings

    if not settings.configured:
        # Settings are already configured by DJANGO_SETTINGS_MODULE env var
        django.setup()


def enqueue_django_mode_job(mode: str, queue: str):
    """Enqueue a job using Django's sqlery integration.

    Args:
        mode: The mode name (for the job argument)
        queue: The queue name

    Raises:
        Exception: If enqueuing fails
    """
    from lab_jobs.tasks import ping_job

    logger.info(f"Enqueueing job on Django mode queue={queue}, mode={mode}")
    job = ping_job.enqueue(mode=mode, queue=queue)
    logger.info(f"Enqueued Django job: id={job.id}, queue={job.queue_name}")
    return job.id


def enqueue_standalone_mode_job(mode: str, queue: str):
    """Enqueue a job for the standalone queue using Django's backend.

    The verifier runs in the Django container but needs to enqueue a job that
    will be picked up by the standalone workers (sqlery-web/sqlery-worker in
    the standalone container). Since both Django and standalone modes write to
    the same sqlery_queued_job table in Postgres, we can use the Django backend
    to enqueue the job, and the standalone workers will pick it up and execute it.

    Args:
        mode: The mode name (for the job argument)
        queue: The queue name (standalone_queue)

    Raises:
        Exception: If enqueuing fails
    """
    from lab_jobs.tasks import ping_job

    logger.info(f"Enqueueing job on standalone mode queue={queue}, mode={mode}")

    # Use Django's public job API to enqueue on the standalone queue
    job = ping_job.enqueue(mode=mode, queue=queue)
    logger.info(f"Enqueued job for standalone queue: id={job.id}, queue={job.queue_name}")
    return job.id


def poll_job_status(db_url: str, job_id: int, queue_name: str, timeout_seconds: int = 60):
    """Poll the sqlery_queued_job table until job reaches terminal status.

    Args:
        db_url: Database URL (uses psycopg directly for framework-agnostic access)
        job_id: Job ID to check
        queue_name: Queue name for context
        timeout_seconds: How long to wait before timing out

    Returns:
        Tuple of (status_str, detail_str) where status_str is 'completed', 'failed', or 'timeout'
    """
    start_time = time.time()
    poll_interval = 1.0  # seconds between checks

    while time.time() - start_time < timeout_seconds:
        try:
            # Use psycopg to query the database directly (framework-agnostic)
            with psycopg.connect(db_url) as conn:
                with conn.cursor() as cur:
                    # Query the most recent job on this queue (ordered by created_at DESC)
                    # to handle cases where multiple jobs might be enqueued
                    cur.execute(
                        """
                        SELECT id, status, output, error
                        FROM sqlery_queued_job
                        WHERE queue_name = %s AND id = %s
                        LIMIT 1
                        """,
                        (queue_name, job_id),
                    )
                    row = cur.fetchone()

            if not row:
                # Job row not found yet
                logger.debug(f"Job {job_id} not found in DB yet, polling...")
                time.sleep(poll_interval)
                continue

            db_id, status, output, error = row
            logger.info(f"Job {job_id} status: {status}")

            # Terminal statuses (success is the target terminal status)
            if status == "success":
                return ("success", f"Job completed with output: {output[:100]}")
            elif status == "failed":
                return ("failed", f"Job failed: {error[:100]}")
            elif status in ("running", "queued"):
                # Still processing, wait and retry
                logger.debug(f"Job {job_id} still {status}, polling...")
                time.sleep(poll_interval)
                continue
            else:
                # Unknown status
                return ("failed", f"Unexpected status: {status}")

        except psycopg.OperationalError as e:
            logger.warning(f"Database connection error: {e}, retrying...")
            time.sleep(poll_interval)
            continue
        except Exception as e:
            logger.error(f"Error polling job {job_id}: {e}", exc_info=True)
            return ("failed", f"Error polling job: {str(e)}")

    return ("timeout", f"Job did not reach terminal status within {timeout_seconds}s")


def main():
    """Main verification loop."""
    logger.info(f"Starting verification of {len(QUEUES)} modes (timeout={VERIFY_TIMEOUT_SECONDS}s)")

    # Validate database URLs
    if not DATABASE_URL:
        logger.error("DATABASE_URL not set")
        sys.exit(1)
    if not STANDALONE_DB_URL:
        logger.error("LAB_STANDALONE_DB_URL not set")
        sys.exit(1)

    # Set up Django for the Django queues
    try:
        setup_django()
        logger.info("Django initialized")
    except Exception as e:
        logger.error(f"Failed to initialize Django: {e}", exc_info=True)
        sys.exit(1)

    # Track results
    results = {}
    job_ids = {}

    # Phase 1: Enqueue jobs on all queues
    for queue_name in QUEUES:
        mode_name = queue_name.replace("_queue", "")
        logger.info(f"\n--- Enqueueing job for mode: {mode_name} ---")

        try:
            if queue_name == "standalone_queue":
                job_id = enqueue_standalone_mode_job(mode_name, queue_name)
            else:
                job_id = enqueue_django_mode_job(mode_name, queue_name)

            job_ids[queue_name] = job_id
            results[queue_name] = ("pending", "Waiting for execution...")

        except Exception as e:
            logger.error(f"Failed to enqueue job for {queue_name}: {e}", exc_info=True)
            results[queue_name] = ("failed", f"Enqueue error: {str(e)}")

    # Phase 2: Poll all jobs for completion
    logger.info("\n--- Polling jobs for completion ---")
    for queue_name in QUEUES:
        if queue_name not in job_ids:
            continue  # Skip queues that failed to enqueue

        logger.info(f"Polling {queue_name} (job_id={job_ids[queue_name]})")
        status, detail = poll_job_status(
            DATABASE_URL,
            job_ids[queue_name],
            queue_name,
            timeout_seconds=VERIFY_TIMEOUT_SECONDS,
        )
        results[queue_name] = (status, detail)

    # Phase 3: Print results table
    logger.info("\n" + "=" * 80)
    logger.info("VERIFICATION RESULTS")
    logger.info("=" * 80)

    passed_count = 0
    total_count = len(QUEUES)

    for queue_name in QUEUES:
        if queue_name in results:
            status, detail = results[queue_name]
            status_str = "PASS" if status == "success" else "FAIL"
            if status == "success":
                passed_count += 1

            mode_name = queue_name.replace("_queue", "").upper()
            logger.info(f"{mode_name:<20} {status_str:<6} {detail}")
        else:
            logger.info(f"{queue_name.replace('_queue', '').upper():<20} {'FAIL':<6} Not attempted")

    # Summary line
    logger.info("=" * 80)
    logger.info(f"SUMMARY: {passed_count}/{total_count} modes passed")
    logger.info("=" * 80)

    # Exit with appropriate code
    sys.exit(0 if passed_count == total_count else 1)


if __name__ == "__main__":
    main()
