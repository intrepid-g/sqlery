"""AWS Lambda handler for serverless job processing.

This module provides the Lambda handler function for processing sqlery jobs
in a serverless environment.

Deployment:
----------
1. Package your Django app with this handler
2. Deploy to AWS Lambda with the following configuration:
   - Handler: sqlery.lambda_handler.handler
   - Timeout: Appropriate for your longest job (e.g., 15 minutes)
   - Memory: Based on your job requirements
   - Environment variables: DJANGO_SETTINGS_MODULE, DATABASE_URL, etc.

3. Configure Lambda permissions:
   - Allow Lambda to be invoked by EventBridge
   - Allow Lambda to invoke itself (for recursive worker spawning)
   - Database access permissions

Example serverless.yml:
----------------------
functions:
  worker:
    handler: sqlery.lambda_handler.handler
    timeout: 900  # 15 minutes
    memorySize: 1024
    environment:
      DJANGO_SETTINGS_MODULE: myproject.settings
      DATABASE_URL: ${env:DATABASE_URL}
    events:
      - schedule:
          rate: rate(5 minutes)  # Regular polling for jobs
          enabled: true

Event Payload:
-------------
{
  "action": "process_queue",    # Process queued jobs
  "job_id": 123,                # Optional: specific job ID
  "queue_name": "default"       # Optional: specific queue
}

or

{
  "action": "run_scheduled_task",  # Run a specific scheduled task
  "task_id": 456,
  "task_path": "myapp.tasks.backup",
  "queue_name": "default",
  "priority": 10
}
"""

import json
import logging
import os
import sys

import django
from django.db import transaction
from django.utils import timezone

from .eventbridge_trigger import (
    ensure_cron_eventbridge_rule,
    invoke_lambda_worker,
)
from sqlery.core.worker import TaskExecutor
from .models import QueuedJob, ScheduledTask

logger = logging.getLogger(__name__)


def handler(event, context):
    """AWS Lambda handler for sqlery job processing.

    Args:
        event: Lambda event payload
        context: Lambda context object

    Returns:
        dict: Response with execution details
    """
    # Initialize Django
    setup_django()

    logger.info(f"Lambda invoked with event: {json.dumps(event)}")

    action = event.get("action", "process_queue")

    # Plan 02-08 (DMOD-04): for the common queue-processing actions, delegate
    # to the mode-agnostic helper so the Django and standalone Lambda twins
    # share one claim+execute path. ``run_scheduled_task`` still uses the
    # legacy Django-specific dispatcher (it touches Django-only models like
    # ScheduledTask + EventBridge plumbing).
    if action in ("process_queue", "poll_and_process"):
        from sqlery.compat import get_backend
        from sqlery.core.lambda_core import process_event
        return process_event(event, get_backend())

    elif action == "run_scheduled_task":
        return run_scheduled_task_action(event, context)

    else:
        error_msg = f"Unknown action: {action}"
        logger.error(error_msg)
        return {"statusCode": 400, "body": json.dumps({"error": error_msg})}


def setup_django():
    """Initialize Django for Lambda environment."""
    # Ensure Django settings are configured
    settings_module = os.environ.get("DJANGO_SETTINGS_MODULE")
    if not settings_module:
        raise ValueError("DJANGO_SETTINGS_MODULE environment variable must be set")

    # import django  # moved to top-level

    if not django.conf.settings.configured:
        django.setup()


def process_queue_action(event, context):
    """Process jobs from the queue.

    Event payload:
        {
          "action": "process_queue",
          "job_id": 123,           # Optional
          "queue_name": "default"  # Optional
        }
    """
    # from .executor import TaskExecutor  # moved to top-level
    # from .models import QueuedJob  # moved to top-level
    # from .eventbridge_trigger import invoke_lambda_worker  # moved to top-level

    executor = TaskExecutor()

    job_id = event.get("job_id")
    queue_name = event.get("queue_name")

    if job_id:
        # Process specific job
        try:
            job = QueuedJob.objects.get(id=job_id)
            result = executor.execute_job(job)

            logger.info(f"Processed job {job_id}: {result}")

            # Check if there are more jobs in the queue
            # If yes, spawn another Lambda to process them
            more_jobs = QueuedJob.objects.filter(
                status="queued",
                queue_name=job.queue_name,
            ).exists()

            if more_jobs:
                logger.info(f"More jobs in queue '{job.queue_name}', invoking another worker")
                invoke_lambda_worker(queue_name=job.queue_name)

            return {
                "statusCode": 200,
                "body": json.dumps({
                    "job_id": job_id,
                    "status": result.get("status"),
                    "duration": result.get("duration"),
                }),
            }

        except QueuedJob.DoesNotExist:
            logger.warning(f"Job {job_id} not found")
            return {
                "statusCode": 404,
                "body": json.dumps({"error": f"Job {job_id} not found"}),
            }

    else:
        # Process next available job(s) from queue
        result = executor.run_queue_workers(queue_name=queue_name, once=True, max_jobs=1)

        jobs_processed = result.get("jobs_processed", 0)

        logger.info(f"Processed {jobs_processed} jobs from queue '{queue_name or 'all'}'")

        # Check if there are more jobs and spawn another worker
        more_jobs = QueuedJob.objects.filter(
            status="queued",
            queue_name=queue_name if queue_name else None,
        ).exists()

        if more_jobs and jobs_processed > 0:
            logger.info("More jobs available, invoking another worker")
            invoke_lambda_worker(queue_name=queue_name)

        return {
            "statusCode": 200,
            "body": json.dumps({
                "jobs_processed": jobs_processed,
                "queue_name": queue_name or "all",
            }),
        }


def run_scheduled_task_action(event, context):
    """Run a specific scheduled task (invoked by EventBridge cron rule).

    Event payload:
        {
          "action": "run_scheduled_task",
          "task_id": 456,
          "task_path": "myapp.tasks.backup",
          "queue_name": "default",
          "priority": 10
        }
    """
    # from .models import ScheduledTask, QueuedJob  # moved to top-level
    # from .eventbridge_trigger import ensure_cron_eventbridge_rule, invoke_lambda_worker  # moved to top-level
    # from django.db import transaction  # moved to top-level

    task_id = event.get("task_id")

    if not task_id:
        error_msg = "task_id is required for run_scheduled_task action"
        logger.error(error_msg)
        return {"statusCode": 400, "body": json.dumps({"error": error_msg})}

    try:
        task = ScheduledTask.objects.get(id=task_id)

        # Check if task is still enabled
        if not task.enabled:
            logger.info(f"Scheduled task {task_id} is disabled, skipping")
            return {
                "statusCode": 200,
                "body": json.dumps({"message": "Task disabled, skipped"}),
            }

        # Enqueue the job
        with transaction.atomic():
            # Check if already queued
            existing_queued = QueuedJob.objects.filter(
                scheduled_task=task,
                status__in=["queued", "running"],
            ).exists()

            if existing_queued:
                logger.info(f"Task {task_id} already has queued/running job, skipping")
                return {
                    "statusCode": 200,
                    "body": json.dumps({"message": "Job already queued"}),
                }

            # Create the job
            job = QueuedJob.objects.create(
                task_path=task.task_path,
                queue_name=task.queue_name,
                priority=task.priority,
                scheduled_task=task,
            )

            logger.info(f"Enqueued job {job.id} for scheduled task {task_id}")

        # Ensure EventBridge rule exists for next execution
        ensure_cron_eventbridge_rule(
            task_id=task.id,
            cron_expression=task.cron_expression,
            task_path=task.task_path,
            queue_name=task.queue_name,
            priority=task.priority,
        )

        # Invoke worker to process the job
        invoke_lambda_worker(job_id=job.id)

        return {
            "statusCode": 200,
            "body": json.dumps({
                "task_id": task_id,
                "job_id": job.id,
                "message": "Job enqueued and worker invoked",
            }),
        }

    except ScheduledTask.DoesNotExist:
        logger.error(f"Scheduled task {task_id} not found")
        return {
            "statusCode": 404,
            "body": json.dumps({"error": f"Task {task_id} not found"}),
        }


def poll_and_process_action(event, context):
    """Poll for due scheduled tasks and queued jobs, then process them.

    This is useful for periodic Lambda invocations (e.g., every 5 minutes)
    to check for work.

    Event payload:
        {
          "action": "poll_and_process"
        }
    """
    # from .executor import TaskExecutor  # moved to top-level
    # from .eventbridge_trigger import invoke_lambda_worker  # moved to top-level

    executor = TaskExecutor()

    # First, check for due scheduled tasks and enqueue them
    due_jobs = executor.run_due_tasks()

    logger.info(f"Found {len(due_jobs)} due scheduled tasks")

    # Then process queued jobs
    result = executor.run_queue_workers(once=True, max_jobs=1)
    jobs_processed = result.get("jobs_processed", 0)

    logger.info(f"Processed {jobs_processed} queued jobs")

    # If there are more queued jobs, invoke another worker
    # from .models import QueuedJob  # moved to top-level

    more_jobs = QueuedJob.objects.filter(status="queued").exists()

    if more_jobs:
        logger.info("More jobs in queue, invoking another worker")
        invoke_lambda_worker()

    return {
        "statusCode": 200,
        "body": json.dumps({
            "due_tasks_enqueued": len(due_jobs),
            "jobs_processed": jobs_processed,
        }),
    }
