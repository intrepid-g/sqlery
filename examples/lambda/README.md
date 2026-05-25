# Sqlery Lambda/EventBridge Deployment Example

> ⚠️ **Experimental:** The Lambda/serverless mode has only been smoke-tested
> (no LocalStack/SAM fidelity testing) and is **not** production-ready. Validate
> thoroughly in your own environment before relying on it.

This example shows how to deploy Sqlery in a fully serverless mode using AWS Lambda and EventBridge.

## Architecture

```
┌─────────────┐
│ Django App  │  calls enqueue()
│             │────────────────┐
└─────────────┘                │
                               ▼
                        ┌──────────────┐
                        │ Lambda       │  Invoked immediately
                        │ Worker       │  for instant jobs
                        └──────────────┘

┌─────────────┐
│ Django App  │  calls enqueue_at(dt)
│             │────────────────┐
└─────────────┘                │
                               ▼
                        ┌──────────────┐
                        │ EventBridge  │  Delayed event
                        │ Rule         │  triggers at dt
                        └──────┬───────┘
                               │
                               ▼
                        ┌──────────────┐
                        │ Lambda       │  Executes job
                        │ Worker       │  at scheduled time
                        └──────────────┘

┌─────────────┐
│ ScheduledTask│ Cron job created/updated
│ (Cron)      │────────────────┐
└─────────────┘                │
                               ▼
                        ┌──────────────┐
                        │ EventBridge  │  Cron rule
                        │ Rule         │  triggers on schedule
                        └──────┬───────┘
                               │
                               ▼
                        ┌──────────────┐
                        │ Lambda       │  Executes cron job
                        │ Worker       │  on schedule
                        └──────────────┘
```

## Features

- **Fully Serverless**: No always-running processes
- **Event-Driven**: EventBridge triggers Lambda on schedule
- **Auto-Scaling**: Lambda scales automatically with job volume
- **Cost-Effective**: Pay only for execution time
- **Immediate Jobs**: `enqueue()` directly invokes Lambda
- **Delayed Jobs**: `enqueue_at()` schedules EventBridge event
- **Cron Jobs**: Automatically synced to EventBridge rules

## Prerequisites

1. AWS Account
2. AWS CLI configured
3. Serverless Framework installed: `npm install -g serverless`
4. Python 3.13+
5. PostgreSQL database (RDS or Aurora)

## Setup

### 1. Install Dependencies

```bash
pip install sqlery[eventbridge]
```

### 2. Configure Django Settings

```python
# settings.py
DATABASES = {
    'default': dj_database_url.config(
        default=os.environ['DATABASE_URL'],
        conn_max_age=600,
    )
}

DJANGO_SQL_JOBS = {
    "TRIGGER_MODE": "eventbridge",
    "EVENTBRIDGE_LAMBDA_ARN": os.environ.get("LAMBDA_ARN"),
    "EVENTBRIDGE_BUS_NAME": "default",
    "AWS_REGION": "us-east-1",
}

INSTALLED_APPS = [
    # ...
    'sqlery',
]
```

### 3. Create Lambda Package

```bash
# Create a deployment package
mkdir lambda_package
cd lambda_package

# Copy your Django project
cp -r ../myproject .

# Install dependencies
pip install -r requirements.txt -t .

# Create zip
zip -r lambda_package.zip .
```

### 4. Deploy with Serverless Framework

```bash
# Set environment variables
export DATABASE_URL="postgresql://user:pass@host:5432/dbname"
export SECRET_KEY="your-secret-key"

# Deploy
serverless deploy

# Get Lambda ARN from output
export LAMBDA_ARN="arn:aws:lambda:us-east-1:123456789:function:sqlery-worker-dev-worker"
```

### 5. Update Settings with Lambda ARN

```bash
# Update your Django settings or environment variable
export LAMBDA_ARN="arn:aws:lambda:us-east-1:123456789:function:sqlery-worker-dev-worker"
```

## Usage

### Immediate Job Execution

```python
from sqlery import enqueue

# Enqueues job and immediately invokes Lambda
job = enqueue('myapp.tasks.send_email', to='user@example.com')
```

### Delayed Job Execution

```python
from sqlery import enqueue_at
from datetime import datetime, timedelta

# Schedules EventBridge event for 1 hour from now
run_time = datetime.now() + timedelta(hours=1)
job = enqueue_at('myapp.tasks.reminder', run_time, user_id=123)
```

### Cron Jobs

```python
from sqlery.models import ScheduledTask

# Create cron task - EventBridge rule automatically created
task = ScheduledTask.objects.create(
    name='daily_backup',
    task_path='myapp.tasks.backup_database',
    cron_expression='0 2 * * *',  # 2 AM daily
    queue_name='maintenance',
    enabled=True
)

# EventBridge rule is automatically created/updated
# When job completes, next execution is automatically scheduled
```

## Configuration Options

```python
DJANGO_SQL_JOBS = {
    # Required
    "TRIGGER_MODE": "eventbridge",
    "EVENTBRIDGE_LAMBDA_ARN": "arn:aws:lambda:...",

    # Optional
    "EVENTBRIDGE_BUS_NAME": "default",  # EventBridge bus name
    "AWS_REGION": "us-east-1",          # AWS region

    # Standard sqlery settings still apply
    "DEFAULT_QUEUE": "default",
    "DEFAULT_PRIORITY": 0,
    "DEFAULT_MAX_RETRIES": 0,
}
```

## Testing Locally

You can test Lambda locally using AWS SAM:

```bash
# Install SAM CLI
brew install aws-sam-cli

# Invoke locally
sam local invoke SqleryWorker -e events/process_queue.json
```

Example event (`events/process_queue.json`):

```json
{
  "action": "process_queue",
  "queue_name": "default"
}
```

## Monitoring

### CloudWatch Logs

Lambda execution logs are automatically sent to CloudWatch:

```bash
# View logs
serverless logs -f worker --tail

# Or use AWS CLI
aws logs tail /aws/lambda/sqlery-worker-dev-worker --follow
```

### EventBridge Rules

View scheduled EventBridge rules in AWS Console:
- Navigate to EventBridge → Rules
- Look for rules prefixed with `sqlery-`

### Database Monitoring

Query job status directly:

```python
from sqlery.models import QueuedJob

# Check recent jobs
recent_jobs = QueuedJob.objects.order_by('-created_at')[:10]

# Failed jobs
failed_jobs = QueuedJob.objects.filter(status='failed')
```

## Cost Optimization

1. **Lambda Memory**: Start with 1024 MB, adjust based on metrics
2. **Lambda Timeout**: Set to max expected job duration + buffer
3. **EventBridge Rules**: Clean up one-time rules after execution
4. **Database Connections**: Use connection pooling (RDS Proxy)
5. **Job Retention**: Configure cleanup to delete old jobs

```python
DJANGO_SQL_JOBS = {
    "JOB_RETENTION": {
        "success_max_age_days": 3,   # Shorter retention = lower DB costs
        "failed_max_age_days": 14,
    },
    "AUTO_CLEANUP_JOBS": True,
}
```

## Troubleshooting

### Lambda Timeout

If jobs are timing out:
1. Increase `timeout` in `serverless.yml`
2. Break large jobs into smaller chunks
3. Use `timeout_seconds` on individual jobs

### EventBridge Rules Not Created

Check Lambda IAM permissions:
- `events:PutRule`
- `events:PutTargets`
- `events:EnableRule`

### Database Connection Issues

For RDS in VPC:
1. Add Lambda to same VPC in `serverless.yml`
2. Configure security groups for database access
3. Consider using RDS Proxy for connection pooling

### Jobs Not Processing

1. Check Lambda logs in CloudWatch
2. Verify `EVENTBRIDGE_LAMBDA_ARN` is correct
3. Ensure Lambda has permission to invoke itself
4. Check database connectivity

## Production Checklist

- [ ] Set appropriate Lambda timeout
- [ ] Configure VPC if database requires it
- [ ] Set up CloudWatch alarms for failures
- [ ] Configure dead letter queue (DLQ)
- [ ] Enable X-Ray tracing for debugging
- [ ] Set up automated cleanup of old jobs
- [ ] Test failover and retry logic
- [ ] Monitor Lambda concurrent executions
- [ ] Set up cost alerts

## Example Task

```python
# myapp/tasks.py
import logging

logger = logging.getLogger(__name__)

def send_email(to, subject, body):
    """Send email task."""
    logger.info(f"Sending email to {to}")

    # Your email sending logic here
    # ...

    logger.info(f"Email sent successfully to {to}")
    return {"status": "sent", "to": to}
```

Usage:

```python
from sqlery import enqueue

# Immediate
enqueue('myapp.tasks.send_email',
        to='user@example.com',
        subject='Welcome!',
        body='Thanks for signing up')

# Delayed (1 hour)
from datetime import datetime, timedelta
enqueue_at('myapp.tasks.send_email',
           datetime.now() + timedelta(hours=1),
           to='user@example.com',
           subject='Reminder',
           body='Your trial expires soon')
```
