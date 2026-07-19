#!/usr/bin/env python3
"""Subprocess-mode loop driver for examples/run_modes_lab.

Periodically enqueues jobs and triggers worker execution via subprocess spawning.
Execution mode is controlled by EXECUTION_MODE=subprocess set in config/settings.py.
"""

import os
import sys
import time
import logging
import threading
import signal

import django

# Initialize Django before any sqlery imports
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from lab_jobs.tasks import ping_job
from sqlery.triggers import trigger_queue_workers

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

# Graceful shutdown via SIGTERM
shutdown_event = threading.Event()


def handle_sigterm(signum, frame):
    """Handle SIGTERM signal for graceful shutdown."""
    logger.info("Received SIGTERM, shutting down gracefully...")
    shutdown_event.set()


signal.signal(signal.SIGTERM, handle_sigterm)


def main():
    """Main loop: enqueue jobs and trigger workers at regular intervals."""
    # Validate required env vars
    queue_name = os.environ.get("LAB_QUEUE")
    if not queue_name:
        logger.error("LAB_QUEUE environment variable is required")
        sys.exit(1)

    # Read optional env vars
    try:
        tick_seconds = int(os.environ.get("LAB_TICK_SECONDS", "5"))
    except ValueError:
        logger.error("LAB_TICK_SECONDS must be an integer")
        sys.exit(1)

    logger.info(f"Starting subprocess loop: queue={queue_name}, tick_seconds={tick_seconds}")

    while not shutdown_event.is_set():
        try:
            # Enqueue a job to ensure queue has work
            ping_job.enqueue(mode=queue_name, queue=queue_name)

            # Trigger worker execution in subprocess
            trigger_queue_workers(queue_name=queue_name)

            logger.info(f"tick queue={queue_name} mode=subprocess")
        except Exception as e:
            logger.exception(f"Error during tick for queue {queue_name}: {e}")

        # Sleep before next tick, but allow graceful shutdown
        shutdown_event.wait(tick_seconds)


if __name__ == "__main__":
    main()
