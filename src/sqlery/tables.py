"""Centralized table name constants for sqlery.

All table names are defined here to avoid hardcoding across the codebase.
"""

QUEUED_JOB = "sqlery_queued_job"
SCHEDULED_TASK = "sqlery_scheduled_task"
SCHEDULED_JOB = "sqlery_scheduled_job"
WORKER = "sqlery_worker"
REGISTRY = "sqlery_registry"
DAEMON_LEASE = "sqlery_daemon_lease"
JOB_ATTEMPT = "sqlery_job_attempt"
JOB_READY = "sqlery_job_ready"
JOB_BLOCKED = "sqlery_job_blocked"
JOB_HISTORY = "sqlery_job_history"
JOB_ALL = "sqlery_job_all"
