"""EventBridge trigger for serverless/Lambda deployments.

This module enables fully serverless, event-driven job processing using AWS Lambda and EventBridge.

Architecture:
-----------
1. Enqueue(job): Directly invokes Lambda worker function for immediate execution
2. Enqueue_at(job, dt): Schedules delayed event via EventBridge
3. Cron jobs: Worker ensures EventBridge rules are created for next execution

Usage:
-----
Configure in Django settings:

DJANGO_SQL_JOBS = {
    "TRIGGER_MODE": "eventbridge",
    "EVENTBRIDGE_LAMBDA_ARN": "arn:aws:lambda:us-east-1:123456789:function:my-worker",
    "EVENTBRIDGE_BUS_NAME": "default",  # Optional, defaults to "default"
    "AWS_REGION": "us-east-1",  # Optional, uses boto3 defaults if not set
}

Then use the standard API:
    from sqlery import enqueue, enqueue_at

    # Immediately invokes Lambda
    job = enqueue('myapp.tasks.send_email', to='user@example.com')

    # Schedules EventBridge delayed event
    job = enqueue_at('myapp.tasks.reminder', run_time, user_id=123)
"""

import json
import logging
from datetime import datetime
from typing import Any

from django.utils import timezone

from .settings import get_setting

try:
    import boto3
except ImportError:
    boto3 = None

logger = logging.getLogger(__name__)


def invoke_lambda_worker(job_id: int | None = None, queue_name: str | None = None) -> dict[str, Any]:
    """Directly invoke Lambda worker function for immediate job processing.

    Args:
        job_id: Specific job ID to process (optional)
        queue_name: Queue name to process (optional)

    Returns:
        dict: Response from Lambda invocation
    """
    # import boto3  # moved to top-level
    # from .settings import get_setting  # moved to top-level

    lambda_arn = get_setting("EVENTBRIDGE_LAMBDA_ARN")
    if not lambda_arn:
        raise ValueError("EVENTBRIDGE_LAMBDA_ARN setting is required for EventBridge trigger mode")

    aws_region = get_setting("AWS_REGION", None)

    # Create Lambda client
    lambda_client = boto3.client("lambda", region_name=aws_region)

    # Prepare payload
    payload = {
        "action": "process_queue",
        "job_id": job_id,
        "queue_name": queue_name,
    }

    # Extract function name from ARN
    function_name = lambda_arn.split(":")[-1]

    logger.info(f"Invoking Lambda function {function_name} with payload: {payload}")

    # Invoke Lambda asynchronously (Event invocation type)
    response = lambda_client.invoke(
        FunctionName=function_name,
        InvocationType="Event",  # Async invocation (fire and forget)
        Payload=json.dumps(payload).encode("utf-8"),
    )

    return {
        "status_code": response["StatusCode"],
        "request_id": response.get("ResponseMetadata", {}).get("RequestId"),
    }


def schedule_eventbridge_event(job_id: int, run_at: datetime) -> dict[str, Any]:
    """Schedule a delayed event using EventBridge for job execution.

    Args:
        job_id: QueuedJob ID to execute
        run_at: Datetime when the job should be executed

    Returns:
        dict: Response with rule ARN and schedule expression
    """
    # import boto3  # moved to top-level
    # from django.utils import timezone  # moved to top-level
    # from .settings import get_setting  # moved to top-level

    lambda_arn = get_setting("EVENTBRIDGE_LAMBDA_ARN")
    if not lambda_arn:
        raise ValueError("EVENTBRIDGE_LAMBDA_ARN setting is required for EventBridge trigger mode")

    bus_name = get_setting("EVENTBRIDGE_BUS_NAME", "default")
    aws_region = get_setting("AWS_REGION", None)

    # Create EventBridge client
    events_client = boto3.client("events", region_name=aws_region)

    # Ensure timezone-aware
    if run_at.tzinfo is None:
        run_at = run_at.replace(tzinfo=timezone.utc)
    else:
        run_at = run_at.astimezone(timezone.utc)

    # Create unique rule name
    rule_name = f"sqlery-job-{job_id}-{int(run_at.timestamp())}"

    # EventBridge schedule expression (one-time execution)
    # Format: cron(minute hour day month day-of-week year)
    schedule_expression = (
        f"cron({run_at.minute} {run_at.hour} {run_at.day} "
        f"{run_at.month} ? {run_at.year})"
    )

    logger.info(
        f"Creating EventBridge rule '{rule_name}' "
        f"with schedule: {schedule_expression}"
    )

    # Create the rule
    rule_response = events_client.put_rule(
        Name=rule_name,
        ScheduleExpression=schedule_expression,
        State="ENABLED",
        Description=f"Sqlery delayed job {job_id}",
        EventBusName=bus_name,
    )

    # Add Lambda as target
    target_input = json.dumps({
        "action": "process_queue",
        "job_id": job_id,
    })

    events_client.put_targets(
        Rule=rule_name,
        EventBusName=bus_name,
        Targets=[
            {
                "Id": "1",
                "Arn": lambda_arn,
                "Input": target_input,
            }
        ],
    )

    logger.info(f"EventBridge rule created: {rule_response['RuleArn']}")

    return {
        "rule_arn": rule_response["RuleArn"],
        "rule_name": rule_name,
        "schedule_expression": schedule_expression,
    }


def ensure_cron_eventbridge_rule(
    task_id: int,
    cron_expression: str,
    task_path: str,
    queue_name: str,
    priority: int,
) -> dict[str, Any]:
    """Ensure an EventBridge rule exists for a cron scheduled task.

    This is called after a cron job completes to ensure the next execution is scheduled.

    Args:
        task_id: ScheduledTask ID
        cron_expression: Cron expression (e.g., "0 9 * * *")
        task_path: Python path to the task
        queue_name: Queue name
        priority: Job priority

    Returns:
        dict: Response with rule ARN
    """
    # import boto3  # moved to top-level
    # from .settings import get_setting  # moved to top-level

    lambda_arn = get_setting("EVENTBRIDGE_LAMBDA_ARN")
    if not lambda_arn:
        raise ValueError("EVENTBRIDGE_LAMBDA_ARN setting is required for EventBridge trigger mode")

    bus_name = get_setting("EVENTBRIDGE_BUS_NAME", "default")
    aws_region = get_setting("AWS_REGION", None)

    # Create EventBridge client
    events_client = boto3.client("events", region_name=aws_region)

    # Create rule name (stable, so we can update it)
    rule_name = f"sqlery-cron-task-{task_id}"

    # Convert crontab to EventBridge cron format
    # Crontab: minute hour day month day-of-week
    # EventBridge: minute hour day month day-of-week year
    eventbridge_cron = f"cron({cron_expression} *)"

    logger.info(
        f"Creating/updating EventBridge rule '{rule_name}' "
        f"with schedule: {eventbridge_cron}"
    )

    # Create or update the rule
    rule_response = events_client.put_rule(
        Name=rule_name,
        ScheduleExpression=eventbridge_cron,
        State="ENABLED",
        Description=f"Sqlery cron task {task_id}: {task_path}",
        EventBusName=bus_name,
    )

    # Add Lambda as target with task details
    target_input = json.dumps({
        "action": "run_scheduled_task",
        "task_id": task_id,
        "task_path": task_path,
        "queue_name": queue_name,
        "priority": priority,
    })

    events_client.put_targets(
        Rule=rule_name,
        EventBusName=bus_name,
        Targets=[
            {
                "Id": "1",
                "Arn": lambda_arn,
                "Input": target_input,
            }
        ],
    )

    logger.info(f"EventBridge cron rule ensured: {rule_response['RuleArn']}")

    return {
        "rule_arn": rule_response["RuleArn"],
        "rule_name": rule_name,
        "schedule_expression": eventbridge_cron,
    }


def delete_eventbridge_rule(rule_name: str) -> None:
    """Delete an EventBridge rule (e.g., for one-time delayed jobs after execution).

    Args:
        rule_name: Name of the rule to delete
    """
    # import boto3  # moved to top-level
    # from .settings import get_setting  # moved to top-level

    bus_name = get_setting("EVENTBRIDGE_BUS_NAME", "default")
    aws_region = get_setting("AWS_REGION", None)

    events_client = boto3.client("events", region_name=aws_region)

    try:
        # Remove targets first
        events_client.remove_targets(
            Rule=rule_name,
            EventBusName=bus_name,
            Ids=["1"],
        )

        # Then delete the rule
        events_client.delete_rule(
            Name=rule_name,
            EventBusName=bus_name,
        )

        logger.info(f"Deleted EventBridge rule: {rule_name}")
    except Exception as e:
        logger.warning(f"Failed to delete EventBridge rule {rule_name}: {e}")


def disable_cron_eventbridge_rule(task_id: int) -> None:
    """Disable an EventBridge cron rule (e.g., when a scheduled task is disabled).

    Args:
        task_id: ScheduledTask ID
    """
    # import boto3  # moved to top-level
    # from .settings import get_setting  # moved to top-level

    bus_name = get_setting("EVENTBRIDGE_BUS_NAME", "default")
    aws_region = get_setting("AWS_REGION", None)

    events_client = boto3.client("events", region_name=aws_region)
    rule_name = f"sqlery-cron-task-{task_id}"

    try:
        events_client.disable_rule(
            Name=rule_name,
            EventBusName=bus_name,
        )
        logger.info(f"Disabled EventBridge rule: {rule_name}")
    except Exception as e:
        logger.warning(f"Failed to disable EventBridge rule {rule_name}: {e}")
