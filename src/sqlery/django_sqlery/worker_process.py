"""Individual worker subprocess that processes jobs from the queue."""

import logging
import os
import signal
import sys
import time
import traceback

import django

# Setup Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mysite.settings")
django.setup()

from django.utils import timezone

from sqlery.core.worker import TaskExecutor
from sqlery.core.claiming import claim_next_job_with_queue_priority, release_job
from .worker_registry import register_worker, unregister_worker, update_heartbeat
from .settings import get_setting
from .deadlines import write_deadline, clear_deadline

logger = logging.getLogger(__name__)

# Global flag for shutdown
shutdown_requested = False


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global shutdown_requested
    shutdown_requested = True
    # print(f"Worker {os.getpid()}: Shutdown signal received, finishing current job...")
    logger.info(f"Worker {os.getpid()}: Shutdown signal received, finishing current job...")


def run_worker():
    """Main worker loop.

    1. Register worker in database
    2. Enter main loop:
       - Send heartbeat
       - Claim next job atomically
       - If job found: execute it
       - If no job: sleep briefly
       - Check for shutdown signal
    3. Unregister on exit
    """
    # Setup signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Register this worker
    worker = register_worker()
    # print(f"Worker {worker.id.hex[:8]} started (PID: {os.getpid()})")
    logger.info(f"Worker {worker.id.hex[:8]} started (PID: {os.getpid()})")

    executor = TaskExecutor()
    heartbeat_interval = get_setting("WORKER_HEARTBEAT_INTERVAL", 5)
    last_heartbeat = time.time()

    try:
        while not shutdown_requested:
            # Send heartbeat if interval elapsed
            if time.time() - last_heartbeat >= heartbeat_interval:
                update_heartbeat(worker)
                last_heartbeat = time.time()

            # Try to claim a job
            job = claim_next_job_with_queue_priority(worker)

            if job:
                # Execute the job
                # print(f"Worker {worker.id.hex[:8]}: Processing job {job.id} [{job.task_path}]")
                logger.info(f"Worker {worker.id.hex[:8]}: Processing job {job.id} [{job.task_path}]")

                # Write deadline file so daemon can enforce timeout externally
                worker_id_str = str(worker.id)
                write_deadline(worker_id_str, job)

                try:
                    result = executor.execute_job(job)

                    # Mark as success
                    release_job(
                        worker,
                        job,
                        status="success",
                        output=str(result) if result else ""
                    )

                    # print(f"Worker {worker.id.hex[:8]}: Job {job.id} completed successfully")
                    logger.info(f"Worker {worker.id.hex[:8]}: Job {job.id} completed successfully")

                except Exception as e:
                    # import traceback  # moved to top-level
                    error_msg = str(e)
                    error_traceback = traceback.format_exc()

                    # Mark as failed
                    release_job(
                        worker,
                        job,
                        status="failed",
                        error=error_msg,
                        traceback=error_traceback
                    )

                    # print(f"Worker {worker.id.hex[:8]}: Job {job.id} failed: {error_msg}")
                    logger.error(f"Worker {worker.id.hex[:8]}: Job {job.id} failed: {error_msg}")

                finally:
                    # Clear deadline file — job is done (success or failure)
                    clear_deadline(worker_id_str)

                # Update heartbeat after job completes
                update_heartbeat(worker)
                last_heartbeat = time.time()

            else:
                # No jobs available - sleep briefly
                time.sleep(1)

    finally:
        # Cleanup on exit
        # print(f"Worker {worker.id.hex[:8]}: Shutting down...")
        logger.info(f"Worker {worker.id.hex[:8]}: Shutting down...")
        unregister_worker(worker)


if __name__ == "__main__":
    run_worker()
