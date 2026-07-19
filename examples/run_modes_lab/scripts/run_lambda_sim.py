#!/usr/bin/env python
"""Lambda-simulation driver for examples/run_modes_lab.

Simulates repeated EventBridge-triggered AWS Lambda invocations by calling the
sqlery.lambda_handler.handler() function directly in a loop (no real AWS).

Env vars:
  LAB_QUEUE             Queue name for job processing (default: lambda_queue)
  LAB_TICK_SECONDS      Sleep interval between simulated Lambda invocations (default: 5)
"""

import json
import logging
import os
import signal
import sys
import time

import django

logger = logging.getLogger(__name__)


def _setup_logging():
    """Configure basic logging for the script."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


def _setup_django():
    """Initialize Django for access to the database and job models."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    if not django.conf.settings.configured:
        django.setup()


def _enqueue_ping_job(queue_name: str):
    """Enqueue a single ping_job onto the given queue."""
    from lab_jobs.tasks import ping_job

    try:
        # ping_job(mode: str) — the kwarg must be `mode`, not `queue_name`;
        # `queue` is the enqueue()-level routing arg, `mode` is the task's own arg.
        ping_job.enqueue(mode=queue_name, queue=queue_name)
        logger.info(f"Enqueued ping_job to {queue_name}")
    except Exception as e:
        logger.error(f"Failed to enqueue ping_job: {e}")


def main():
    """Main loop: simulate Lambda invocations by calling handler() directly."""
    _setup_logging()

    # Setup Django first (required by lambda_handler)
    _setup_django()

    # Import handler after Django setup
    from sqlery.lambda_handler import handler

    # Retrieve configuration
    lab_queue = os.environ.get("LAB_QUEUE", "lambda_queue")
    lab_tick_seconds = int(os.environ.get("LAB_TICK_SECONDS", "5"))

    logger.info(
        f"Lambda simulator starting: queue={lab_queue}, tick={lab_tick_seconds}s"
    )
    logger.info(
        "Note: This simulates AWS Lambda/EventBridge polling by calling the "
        "handler function directly in-process — no real AWS involved."
    )

    # Graceful shutdown handler
    shutdown_event = False

    def handle_sigterm(signum, frame):
        nonlocal shutdown_event
        shutdown_event = True
        logger.info("SIGTERM received, shutting down gracefully")

    signal.signal(signal.SIGTERM, handle_sigterm)

    # Main loop
    while not shutdown_event:
        try:
            # Enqueue a job
            _enqueue_ping_job(lab_queue)

            # Simulate Lambda invocation: call handler with poll_and_process action
            # and optional queue_name parameter
            event = {
                "action": "poll_and_process",
                "queue_name": lab_queue,
            }
            result = handler(event, None)

            logger.info(f"Lambda handler returned: {json.dumps(result)}")

            # Sleep until next simulated invocation
            time.sleep(lab_tick_seconds)

        except KeyboardInterrupt:
            logger.info("Interrupted by user")
            break
        except Exception as e:
            logger.error(f"Unexpected error in main loop: {e}")
            time.sleep(lab_tick_seconds)


if __name__ == "__main__":
    main()
