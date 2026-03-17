"""Settings for sqlery."""

from django.conf import settings


DEFAULTS = {
    # Scheduler settings
    "ENABLE_MIDDLEWARE_TRIGGER": True,
    "CHECK_INTERVAL_SECONDS": 60,  # Check for due tasks every 60 seconds

    # Queue settings
    "DEFAULT_QUEUE": "default",
    "DEFAULT_PRIORITY": 0,
    "AUTO_TRIGGER_WORKER": False,  # Auto-trigger worker after enqueue

    # Execution settings
    "USE_DJANGO_TASKS": True,  # Use django-tasks for async execution
    "EXECUTION_MODE": "auto",  # 'auto', 'subprocess', 'thread', 'django-tasks'
    "MAX_JOBS_PER_RUN": 100,  # For --once mode

    # HTTP trigger settings (for ASGI/async deployments)
    "TRIGGER_MODE": "middleware",  # 'middleware', 'http', 'subprocess', 'daemon', 'eventbridge', or 'disabled'
    "INTERNAL_BASE_URL": None,  # e.g., 'http://127.0.0.1:8000' (required for http mode)
    "INTERNAL_SECRET": None,  # Shared secret for HMAC signatures (required for http mode)
    "SIGNATURE_MAX_AGE": 5,  # Signature validity in seconds

    # EventBridge trigger settings (for serverless/Lambda deployments)
    "EVENTBRIDGE_LAMBDA_ARN": None,  # ARN of Lambda worker function (required for eventbridge mode)
    "EVENTBRIDGE_BUS_NAME": "default",  # EventBridge bus name
    "AWS_REGION": None,  # AWS region (uses boto3 defaults if not set)

    # Daemon settings (for background worker mode)
    "ENABLE_DAEMON": False,  # Enable background daemon worker
    "DAEMON_CHECK_INTERVAL": 10,  # Daemon checks for jobs every 10 seconds

    # Worker pool settings
    "MAX_WORKERS_PER_NODE": 1,  # Number of worker subprocess processes per node
    "WORKER_HEARTBEAT_INTERVAL": 5,  # Worker heartbeat interval in seconds
    "WORKER_POLL_INTERVAL": 5,  # Seconds between job polls when idle
    "WORKER_ALIVE_TIMEOUT": 30,  # Worker is considered dead after this many seconds without heartbeat
    "WORKER_QUEUES": ["high", "default", "low"],  # Queue priority order for workers
    "QUEUE_PRIORITIES": {  # Queue priority weights (higher = processed first)
        "high": 100,
        "default": 50,
        "low": 10,
    },

    # Registry settings (RQ-compatible)
    "ENABLE_REGISTRIES": True,  # Enable job lifecycle tracking with registries
    "REGISTRY_RETENTION": {  # Retention periods for each registry type (days)
        "finished": 7,
        "failed": 30,
        "started": 1,
        "canceled": 7,
        "scheduled": 30,
        "deferred": 30,
    },
    "AUTO_CLEANUP_REGISTRIES": True,  # Automatically cleanup old registry entries

    # Job retention & cleanup settings
    "JOB_RETENTION": {
        # Age-based retention (delete jobs older than N days)
        "success_max_age_days": 7,  # Keep successful jobs for 7 days
        "failed_max_age_days": 30,  # Keep failed jobs for 30 days (for debugging)

        # Count-based retention (keep only N most recent jobs)
        "success_max_count": 10000,  # Keep last 10k successful jobs
        "failed_max_count": 5000,  # Keep last 5k failed jobs

        # Default fallbacks
        "default_max_age_days": 30,
        "default_max_count": 10000,
    },
    "AUTO_CLEANUP_JOBS": True,  # Automatically cleanup old jobs (runs in daemon)
    "CLEANUP_INTERVAL_HOURS": 24,  # How often to run auto-cleanup (in hours)

    # Timeout settings
    "DEFAULT_TIMEOUT_SECONDS": 600,  # Default job timeout: 10 minutes

    # Retry settings
    "DEFAULT_MAX_RETRIES": 0,  # Default number of retries for failed jobs
    "DEFAULT_RETRY_BACKOFF": 1.0,  # Default backoff multiplier for retries

    # Tag-based concurrency limits
    "TAG_CONCURRENCY_LIMITS": {},  # e.g., {"acme-api": 1, "legacy-db": 2, "image-processing": 5}

    # Tag-based rate limits (throttling)
    "TAG_RATE_LIMITS": {},  # e.g., {"acme-api": "60/m", "stripe-api": "100/s", "slow-api": "1/10s"}

    # DB resilience settings
    "DB_RETRY_MAX_ATTEMPTS": 3,       # Retries on transient DB errors
    "DB_RETRY_BACKOFF_BASE": 0.1,     # Base seconds for exponential backoff
    "SQLITE_BUSY_TIMEOUT_MS": 5000,   # SQLite busy_timeout pragma (ms)
    "SQLITE_WAL_MODE": True,          # Enable WAL journal mode for SQLite
    "PG_STATEMENT_TIMEOUT_MS": 30000, # PostgreSQL statement_timeout (ms), 0=disabled
    "PG_LOCK_TIMEOUT_MS": 10000,      # PostgreSQL lock_timeout (ms), 0=disabled
}


def migrate_settings(
    scheduler_config: dict | None = None,
    scheduler_queues: dict | None = None,
    **overrides,
) -> dict:
    """Convert RQ / Django-Tasks-Scheduler settings into DJANGO_SQL_JOBS.

    Reads old-style config dicts and produces a ready-to-use DJANGO_SQL_JOBS
    dict with sensible defaults for everything not specified.

    Usage in settings.py::

        # Option A — migrate from existing RQ/Scheduler config:
        DJANGO_SQL_JOBS = migrate_settings(SCHEDULER_CONFIG, SCHEDULER_QUEUES)

        # Option B — migrate with overrides:
        DJANGO_SQL_JOBS = migrate_settings(
            SCHEDULER_CONFIG, SCHEDULER_QUEUES,
            MAX_WORKERS_PER_NODE=3,
        )

        # Option C — fresh project, just use defaults:
        DJANGO_SQL_JOBS = migrate_settings()

    Args:
        scheduler_config: Old SCHEDULER_CONFIG dict, e.g.::

            {
                'SCHEDULER_INTERVAL': 10,
                'DEFAULT_RESULT_TTL': 500,
                'DEFAULT_TIMEOUT': 600,
            }

        scheduler_queues: Old RQ-style SCHEDULER_QUEUES / RQ_QUEUES dict, e.g.::

            {
                'priority': {'HOST': '...', 'PORT': 6379, 'DB': 0},
                'default':  {'HOST': '...', 'PORT': 6379, 'DB': 0},
            }

        **overrides: Any DJANGO_SQL_JOBS keys to set explicitly.

    Returns:
        A complete DJANGO_SQL_JOBS dict (merged with DEFAULTS).
    """
    result = dict(DEFAULTS)
    scheduler_config = scheduler_config or {}
    scheduler_queues = scheduler_queues or {}

    # -- Queues: extract names and auto-assign priorities --------------------
    if scheduler_queues:
        queue_names = list(scheduler_queues.keys())
        result["WORKER_QUEUES"] = queue_names
        # Auto-assign descending priorities (first queue = highest)
        total = len(queue_names)
        result["QUEUE_PRIORITIES"] = {
            name: max(10, 100 - idx * (90 // max(total - 1, 1)))
            for idx, name in enumerate(queue_names)
        }
        if "default" in queue_names:
            result["DEFAULT_QUEUE"] = "default"
        else:
            result["DEFAULT_QUEUE"] = queue_names[0] if queue_names else "default"

    # -- Scheduler config mappings ------------------------------------------
    if "SCHEDULER_INTERVAL" in scheduler_config:
        result["DAEMON_CHECK_INTERVAL"] = scheduler_config["SCHEDULER_INTERVAL"]

    if "DEFAULT_TIMEOUT" in scheduler_config:
        result["DEFAULT_TIMEOUT_SECONDS"] = scheduler_config["DEFAULT_TIMEOUT"]

    if "DEFAULT_RESULT_TTL" in scheduler_config:
        ttl_seconds = scheduler_config["DEFAULT_RESULT_TTL"]
        ttl_days = max(1, ttl_seconds // 86400)
        retention = dict(result.get("JOB_RETENTION", {}))
        retention["success_max_age_days"] = ttl_days
        result["JOB_RETENTION"] = retention

    # Enable daemon mode by default (replaces RQ workers)
    result["TRIGGER_MODE"] = "daemon"
    result["ENABLE_DAEMON"] = True

    # -- Explicit overrides always win --------------------------------------
    result.update(overrides)

    return result


def get_setting(name, default=None):
    """Get a sqlery setting with fallback protection.

    Looks in Django settings.DJANGO_SQL_JOBS dict, then DEFAULTS.

    Self-healing: If user config fails to load or is invalid, falls back to defaults.
    This ensures SQLery continues working even with misconfigured settings.
    """
    import logging
    logger = logging.getLogger(__name__)

    try:
        user_settings = getattr(settings, "DJANGO_SQL_JOBS", {})

        # Validate that user_settings is a dict
        if not isinstance(user_settings, dict):
            logger.warning(
                f"DJANGO_SQL_JOBS is not a dict (got {type(user_settings).__name__}), "
                f"falling back to defaults"
            )
            user_settings = {}

        if name in user_settings:
            value = user_settings[name]

            # Validate the value isn't None when we have a default
            if value is None and name in DEFAULTS and DEFAULTS[name] is not None:
                logger.warning(
                    f"DJANGO_SQL_JOBS['{name}'] is None, using default: {DEFAULTS[name]}"
                )
                return DEFAULTS[name]

            return value

    except Exception as e:
        logger.error(
            f"Failed to load user setting '{name}': {e}, falling back to defaults"
        )
        # Continue to fallback logic below

    if default is not None:
        return default

    return DEFAULTS.get(name)
