# Environment Variables Reference

Complete reference for all environment variables used by sqlery.

---

## Overview

Sqlery can be configured via:
1. **Django settings** (`DJANGO_SQL_JOBS` dict in `settings.py`)
2. **Environment variables** (documented below)
3. **Programmatic configuration** (FastAPI/standalone mode)

Environment variables are particularly useful for:
- Docker/containerized deployments
- 12-factor app compliance
- Separating config from code
- CI/CD pipelines

---

## Core Environment Variables

### Database Configuration

#### `SQLERY_DATABASE_URL`

**Purpose:** Database connection string for standalone/FastAPI mode

**Format:** Standard database URL format

**Examples:**
```bash
# PostgreSQL (async)
export SQLERY_DATABASE_URL="postgresql+asyncpg://user:password@localhost/dbname"

# PostgreSQL (sync)
export SQLERY_DATABASE_URL="postgresql://user:password@localhost/dbname"

# SQLite (async)
export SQLERY_DATABASE_URL="sqlite+aiosqlite:///./jobs.db"

# SQLite (sync)
export SQLERY_DATABASE_URL="sqlite:///./jobs.db"
```

**Default:** None (must be set for standalone mode)

**Used by:** FastAPI mode, standalone mode

**Django:** Not used (Django uses `DATABASES` setting)

---

## Django Mode Environment Variables

All Django settings can be overridden via environment variables using the prefix `DJANGO_SQL_JOBS_`.

### Worker Configuration

#### `DJANGO_SQL_JOBS_MAX_WORKERS`

**Purpose:** Maximum number of worker processes per node

**Type:** Integer

**Example:**
```bash
export DJANGO_SQL_JOBS_MAX_WORKERS=5
```

**Default:** `1`

**Notes:**
- In daemon mode, this many workers will be spawned
- Each worker claims and executes jobs independently
- Higher values = more concurrency but more memory/CPU usage

**Related Django Setting:**
```python
# settings.py equivalent
DJANGO_SQL_JOBS = {
    'MAX_WORKERS_PER_NODE': 5,
}
```

---

#### `DJANGO_SQL_JOBS_WORKER_QUEUES`

**Purpose:** List of queues for workers to process

**Type:** Comma-separated list

**Example:**
```bash
export DJANGO_SQL_JOBS_WORKER_QUEUES="high,default,low"
```

**Default:** `"high,default,low"`

**Notes:**
- Queues are processed in order (left = higher priority)
- Workers will claim jobs from first queue with available jobs

**Related Django Setting:**
```python
DJANGO_SQL_JOBS = {
    'WORKER_QUEUES': ['high', 'default', 'low'],
}
```

---

#### `DJANGO_SQL_JOBS_WORKER_HEARTBEAT_INTERVAL`

**Purpose:** How often workers send heartbeat signals (seconds)

**Type:** Integer

**Example:**
```bash
export DJANGO_SQL_JOBS_WORKER_HEARTBEAT_INTERVAL=10
```

**Default:** `5`

**Notes:**
- Heartbeats prove worker is alive
- Too low = database overhead
- Too high = slow dead worker detection

---

#### `DJANGO_SQL_JOBS_WORKER_ALIVE_TIMEOUT`

**Purpose:** Worker considered dead after this many seconds without heartbeat

**Type:** Integer

**Example:**
```bash
export DJANGO_SQL_JOBS_WORKER_ALIVE_TIMEOUT=60
```

**Default:** `30`

**Notes:**
- Should be > `WORKER_HEARTBEAT_INTERVAL * 2`
- Jobs from dead workers will be reclaimed

---

### Daemon Configuration

#### `DJANGO_SQL_JOBS_ENABLE_DAEMON`

**Purpose:** Enable background daemon worker

**Type:** Boolean (`true`, `false`, `1`, `0`, `yes`, `no`)

**Example:**
```bash
export DJANGO_SQL_JOBS_ENABLE_DAEMON=true
```

**Default:** `false`

**Notes:**
- Set to `true` for daemon mode
- Daemon spawns workers in background
- Requires `TRIGGER_MODE=daemon`

---

#### `DJANGO_SQL_JOBS_CHECK_INTERVAL`

**Purpose:** How often daemon checks for jobs (seconds)

**Type:** Integer

**Example:**
```bash
export DJANGO_SQL_JOBS_CHECK_INTERVAL=30
```

**Default:** `10`

**Notes:**
- Lower = more responsive but higher DB load
- Higher = more efficient but slower job pickup

**Related Django Setting:**
```python
DJANGO_SQL_JOBS = {
    'DAEMON_CHECK_INTERVAL': 30,
}
```

---

### Trigger Mode Configuration

#### `DJANGO_SQL_JOBS_TRIGGER_MODE`

**Purpose:** How jobs are triggered for execution

**Type:** String (enum)

**Valid Values:**
- `middleware` - Trigger on HTTP requests (ASGI/async)
- `http` - HTTP callback to worker
- `subprocess` - Spawn subprocess for each job
- `daemon` - Background daemon workers
- `eventbridge` - AWS EventBridge (serverless)
- `disabled` - Manual execution only

**Example:**
```bash
export DJANGO_SQL_JOBS_TRIGGER_MODE=daemon
```

**Default:** `middleware`

**Notes:**
- `daemon` recommended for production
- `middleware` good for development/low-volume
- `eventbridge` for AWS Lambda deployments

---

### HTTP Trigger Settings (Advanced)

#### `DJANGO_SQL_JOBS_INTERNAL_BASE_URL`

**Purpose:** Base URL for HTTP callbacks in `http` trigger mode

**Type:** URL string

**Example:**
```bash
export DJANGO_SQL_JOBS_INTERNAL_BASE_URL="http://127.0.0.1:8000"
```

**Default:** None

**Required:** Only if `TRIGGER_MODE=http`

**Notes:**
- URL where workers can reach your app
- Used for HTTP callbacks to trigger job execution

---

#### `DJANGO_SQL_JOBS_INTERNAL_SECRET`

**Purpose:** Shared secret for HMAC signatures in `http` trigger mode

**Type:** String (long random secret)

**Example:**
```bash
export DJANGO_SQL_JOBS_INTERNAL_SECRET="your-secret-key-here-at-least-32-chars"
```

**Default:** None

**Required:** Only if `TRIGGER_MODE=http`

**Security:** Keep this secret! Anyone with this can trigger jobs.

---

### EventBridge Settings (AWS Lambda)

#### `DJANGO_SQL_JOBS_EVENTBRIDGE_LAMBDA_ARN`

**Purpose:** ARN of AWS Lambda function to invoke for jobs

**Type:** AWS ARN string

**Example:**
```bash
export DJANGO_SQL_JOBS_EVENTBRIDGE_LAMBDA_ARN="arn:aws:lambda:us-east-1:123456789:function:sqlery-worker"
```

**Default:** None

**Required:** Only if `TRIGGER_MODE=eventbridge`

---

#### `DJANGO_SQL_JOBS_EVENTBRIDGE_BUS_NAME`

**Purpose:** EventBridge bus name

**Type:** String

**Example:**
```bash
export DJANGO_SQL_JOBS_EVENTBRIDGE_BUS_NAME="custom-bus"
```

**Default:** `"default"`

---

#### `DJANGO_SQL_JOBS_AWS_REGION`

**Purpose:** AWS region for EventBridge

**Type:** AWS region code

**Example:**
```bash
export DJANGO_SQL_JOBS_AWS_REGION="us-west-2"
```

**Default:** Uses boto3 defaults (AWS_REGION, ~/.aws/config)

---

### Cleanup & Retention

#### `DJANGO_SQL_JOBS_AUTO_CLEANUP`

**Purpose:** Enable automatic job cleanup

**Type:** Boolean

**Example:**
```bash
export DJANGO_SQL_JOBS_AUTO_CLEANUP=true
```

**Default:** `true`

**Notes:**
- Cleanup runs periodically in daemon
- Deletes old jobs based on retention settings

---

#### `DJANGO_SQL_JOBS_CLEANUP_INTERVAL`

**Purpose:** How often auto-cleanup runs (hours)

**Type:** Integer

**Example:**
```bash
export DJANGO_SQL_JOBS_CLEANUP_INTERVAL=12
```

**Default:** `24`

---

#### `DJANGO_SQL_JOBS_SUCCESS_MAX_AGE_DAYS`

**Purpose:** Delete successful jobs older than N days

**Type:** Integer

**Example:**
```bash
export DJANGO_SQL_JOBS_SUCCESS_MAX_AGE_DAYS=3
```

**Default:** `7`

---

#### `DJANGO_SQL_JOBS_FAILED_MAX_AGE_DAYS`

**Purpose:** Delete failed jobs older than N days

**Type:** Integer

**Example:**
```bash
export DJANGO_SQL_JOBS_FAILED_MAX_AGE_DAYS=14
```

**Default:** `30`

**Notes:** Keep longer for debugging

---

### Job Defaults

#### `DJANGO_SQL_JOBS_DEFAULT_QUEUE`

**Purpose:** Default queue name for jobs

**Type:** String

**Example:**
```bash
export DJANGO_SQL_JOBS_DEFAULT_QUEUE="background"
```

**Default:** `"default"`

---

#### `DJANGO_SQL_JOBS_DEFAULT_MAX_RETRIES`

**Purpose:** Default retry count for failed jobs

**Type:** Integer

**Example:**
```bash
export DJANGO_SQL_JOBS_DEFAULT_MAX_RETRIES=3
```

**Default:** `0`

---

#### `DJANGO_SQL_JOBS_DEFAULT_RETRY_BACKOFF`

**Purpose:** Retry backoff multiplier

**Type:** Float

**Example:**
```bash
export DJANGO_SQL_JOBS_DEFAULT_RETRY_BACKOFF=2.0
```

**Default:** `1.0`

**Notes:**
- `1.0` = no backoff (retry immediately)
- `2.0` = exponential backoff (1s, 2s, 4s, 8s...)

---

## Python Environment Variables

### `DJANGO_SETTINGS_MODULE`

**Purpose:** Django settings module

**Type:** Python module path

**Example:**
```bash
export DJANGO_SETTINGS_MODULE=myproject.settings
```

**Required:** Yes (for Django mode)

**Notes:** Standard Django environment variable

---

### `PYTHONPATH`

**Purpose:** Python module search path

**Type:** Colon-separated paths

**Example:**
```bash
export PYTHONPATH=/app:/app/src
```

**Notes:** Ensure your app is in PYTHONPATH for task imports

---

## Docker Example

Complete Docker environment configuration:

```yaml
# docker-compose.yml
version: '3.8'

services:
  web:
    build: .
    environment:
      # Django
      DJANGO_SETTINGS_MODULE: myproject.settings

      # Database
      DATABASE_URL: postgresql://user:password@db:5432/mydb

      # Sqlery - Daemon Mode
      DJANGO_SQL_JOBS_TRIGGER_MODE: daemon
      DJANGO_SQL_JOBS_ENABLE_DAEMON: "true"
      DJANGO_SQL_JOBS_MAX_WORKERS: "5"
      DJANGO_SQL_JOBS_CHECK_INTERVAL: "10"

      # Sqlery - Queues
      DJANGO_SQL_JOBS_WORKER_QUEUES: "high,default,low"

      # Sqlery - Cleanup
      DJANGO_SQL_JOBS_AUTO_CLEANUP: "true"
      DJANGO_SQL_JOBS_CLEANUP_INTERVAL: "6"
      DJANGO_SQL_JOBS_SUCCESS_MAX_AGE_DAYS: "1"
      DJANGO_SQL_JOBS_FAILED_MAX_AGE_DAYS: "7"

      # Sqlery - Retries
      DJANGO_SQL_JOBS_DEFAULT_MAX_RETRIES: "3"
      DJANGO_SQL_JOBS_DEFAULT_RETRY_BACKOFF: "2.0"
    depends_on:
      - db

  db:
    image: postgres:15
    environment:
      POSTGRES_DB: mydb
      POSTGRES_USER: user
      POSTGRES_PASSWORD: password
```

---

## Kubernetes Example

ConfigMap and Secret for sqlery configuration:

```yaml
# configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: sqlery-config
data:
  DJANGO_SQL_JOBS_TRIGGER_MODE: "daemon"
  DJANGO_SQL_JOBS_ENABLE_DAEMON: "true"
  DJANGO_SQL_JOBS_MAX_WORKERS: "10"
  DJANGO_SQL_JOBS_WORKER_QUEUES: "high,default,low"
  DJANGO_SQL_JOBS_AUTO_CLEANUP: "true"
  DJANGO_SQL_JOBS_CLEANUP_INTERVAL: "12"

---
# secret.yaml
apiVersion: v1
kind: Secret
metadata:
  name: sqlery-secret
type: Opaque
stringData:
  DATABASE_URL: "postgresql://user:password@postgres:5432/mydb"
  DJANGO_SQL_JOBS_INTERNAL_SECRET: "your-secret-here"

---
# deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: django-app
spec:
  replicas: 3
  template:
    spec:
      containers:
      - name: app
        image: myapp:latest
        envFrom:
        - configMapRef:
            name: sqlery-config
        - secretRef:
            name: sqlery-secret
```

---

## Troubleshooting

### Environment Variables Not Taking Effect

**Check:**
1. Variable is exported: `echo $DJANGO_SQL_JOBS_MAX_WORKERS`
2. Process restarted after setting variable
3. Variable name is correct (check spelling)
4. Django setting not overriding env var

**Debug:**
```python
# Django shell
python manage.py shell -c "
from sqlery.django_sqlery.settings import get_setting
print(get_setting('MAX_WORKERS_PER_NODE'))
"
```

### Boolean Values Not Working

**Problem:** `export DJANGO_SQL_JOBS_ENABLE_DAEMON="false"` still enables daemon

**Solution:** Use lowercase or numeric:
```bash
# These work:
export DJANGO_SQL_JOBS_ENABLE_DAEMON=false
export DJANGO_SQL_JOBS_ENABLE_DAEMON=0
export DJANGO_SQL_JOBS_ENABLE_DAEMON=no

# This won't work:
export DJANGO_SQL_JOBS_ENABLE_DAEMON=False  # Treated as truthy!
```

---

## See Also

- [CONFIGURATION.md](CONFIGURATION.md) - Full Django settings reference
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) - Common issues
- [PACKAGE_SPLIT_MIGRATION.md](PACKAGE_SPLIT_MIGRATION.md) - Migration guide

---

**Last Updated:** 2025-11-13
