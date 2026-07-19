#!/usr/bin/env python
"""HTTP-trigger caller for examples/run_modes_lab.

Periodically enqueues jobs and POSTs signed HTTP trigger requests to the Django
HTTP-trigger service. Exercises the HTTP trigger mode for sqlery.

Env vars (required unless noted):
  LAB_HTTP_TARGET       Full URL to the trigger endpoint (e.g. http://http-trigger:8001/internal/trigger/)
  INTERNAL_SECRET       Shared secret for HMAC signing (required for security)
  LAB_QUEUE             Queue name to enqueue jobs into (default: http_queue)
  LAB_TICK_SECONDS      Sleep interval between trigger invocations (default: 5)
"""

import json
import logging
import os
import signal
import sys
import time

import django
import httpx

logger = logging.getLogger(__name__)


def _setup_logging():
    """Configure basic logging for the script."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


def _get_env(name: str, default: str | None = None) -> str:
    """Get an environment variable, failing loudly if required and unset."""
    value = os.environ.get(name, default)
    if value is None:
        logger.error(f"Required environment variable not set: {name}")
        sys.exit(1)
    return value


def _setup_django():
    """Initialize Django for access to the database and job models."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
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


def _post_trigger(
    http_client: httpx.Client, target_url: str, secret: str, queue_name: str
):
    """POST a signed trigger request to the HTTP-trigger service."""
    from sqlery.core.signature import make_signed_request_headers

    # Generate signature headers
    headers = make_signed_request_headers(secret)
    headers["Content-Type"] = "application/json"

    # Build the trigger payload
    body = json.dumps({"action": "process_queue", "queue_name": queue_name})

    try:
        response = http_client.post(target_url, headers=headers, content=body)
        logger.info(f"HTTP trigger POST returned {response.status_code}")
    except httpx.RequestError as e:
        logger.warning(f"HTTP trigger request failed: {e}")
    except Exception as e:
        logger.error(f"Unexpected error in HTTP trigger: {e}")


def main():
    """Main loop: enqueue jobs and trigger via HTTP."""
    _setup_logging()

    # Retrieve configuration
    http_target = _get_env("LAB_HTTP_TARGET")
    internal_secret = _get_env("INTERNAL_SECRET")
    lab_queue = os.environ.get("LAB_QUEUE", "http_queue")
    lab_tick_seconds = int(os.environ.get("LAB_TICK_SECONDS", "5"))

    logger.info(f"HTTP caller starting: target={http_target}, queue={lab_queue}")

    # Setup Django
    _setup_django()

    # Graceful shutdown handler
    shutdown_event = False

    def handle_sigterm(signum, frame):
        nonlocal shutdown_event
        shutdown_event = True
        logger.info("SIGTERM received, shutting down gracefully")

    signal.signal(signal.SIGTERM, handle_sigterm)

    # Main loop
    with httpx.Client() as http_client:
        while not shutdown_event:
            try:
                # Enqueue a job
                _enqueue_ping_job(lab_queue)

                # POST the trigger request
                _post_trigger(http_client, http_target, internal_secret, lab_queue)

                # Sleep until next tick
                time.sleep(lab_tick_seconds)

            except KeyboardInterrupt:
                logger.info("Interrupted by user")
                break
            except Exception as e:
                logger.error(f"Unexpected error in main loop: {e}")
                time.sleep(lab_tick_seconds)


if __name__ == "__main__":
    main()
