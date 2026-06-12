"""Standalone configuration implementation.

Provides in-memory configuration for standalone mode.
"""

import os
from typing import Any

from ..compat import Config


class StandaloneConfig(Config):
    """In-memory configuration for standalone mode.

    Configuration can be set programmatically or via environment variables.
    """

    def __init__(self):
        """Initialize standalone config with defaults."""
        self._config = {
            # Database settings
            'DATABASE_URL': None,

            # Worker settings
            'MAX_WORKERS_PER_NODE': 3,
            'WORKER_QUEUES': ['default'],
            'QUEUE_PRIORITIES': {'default': 50},

            # Daemon settings
            'ENABLE_DAEMON': True,
            'DAEMON_CHECK_INTERVAL': 10,

            # Scheduler jitter (CRON-03): bounded random enqueue delay in seconds.
            # Default 0 = jitter off (PROJECT.md locked). Plan 03 reads this via
            # get_config('scheduler_jitter_seconds', 0) and applies
            # random.uniform(0, jitter) before enqueue. Overridable as a float
            # via SQLERY_SCHEDULER_JITTER_SECONDS.
            'scheduler_jitter_seconds': 0,

            # Connection pool settings (PostgreSQL only)
            'POOL_SIZE': 5,
            'MAX_OVERFLOW': 10,
            'POOL_TIMEOUT': 30,
            'POOL_RECYCLE': 1800,   # 30 min — prevents stale connections after PG idle timeout

            # Retention settings
            'AUTO_CLEANUP_JOBS': True,
            'AUTO_CLEANUP_REGISTRIES': True,
            'JOB_RETENTION': {
                'success': {'max_age_days': 7},
                'failed': {'max_age_days': 30},
            },
            'REGISTRY_RETENTION': {
                'max_age_days': 30,
            },

            # Queue defaults
            'DEFAULT_QUEUE': 'default',
            'DEFAULT_PRIORITY': 0,
            'DEFAULT_MAX_RETRIES': 0,
            'DEFAULT_RETRY_BACKOFF': 1.0,

            # Security (SEC-04): opt-in allowlist for task module imports.
            # None = allow all (BC). Loaded from SQLERY_ALLOWED_TASK_MODULES
            # as comma-separated list (e.g. "myapp,otherapp.tasks").
            'ALLOWED_TASK_MODULES': None,

            # Defense-in-depth IP allowlist for the internal trigger endpoint.
            # Matched against the socket peer (request.client.host), never
            # X-Forwarded-For. Default: loopback only. Sentinel ["*"] (or None)
            # disables the check for deployments that need external access.
            # Loaded from SQLERY_INTERNAL_ALLOWED_IPS as a comma-separated list.
            'INTERNAL_ALLOWED_IPS': ['127.0.0.1', '::1'],

            # ---------------------------------------------------------------
            # Partition configuration (PostgreSQL range-partitioned tables).
            # These settings mirror the Django DEFAULTS in django_sqlery/settings.py
            # and are validated together on first access.
            # ---------------------------------------------------------------

            # Partition granularity: 'monthly' or 'weekly'. Controls how the
            # Alembic helpers create new child partitions. Default: 'monthly'.
            # Loaded from SQLERY_PARTITION_INTERVAL.
            'PARTITION_INTERVAL': 'monthly',

            # Number of future partitions to create in advance. Must be >= 1.
            # Default: 3 (create three partitions ahead of current period).
            # Loaded from SQLERY_PARTITION_PREMAKE.
            'PARTITION_PREMAKE': 3,

            # Partition retention: drop child partitions older than this many
            # months. Must be strictly greater than SCHEDULED_JOB_THRESHOLD_DAYS
            # (converted to months) so the archive window is never narrower than
            # the promotion window.  Default: 24 months.
            # Loaded from SQLERY_PARTITION_RETENTION.
            'PARTITION_RETENTION': 24,

            # Optional Python import path to a callable invoked before a
            # partition is dropped. Signature: hook(table_name, partition_name).
            # None disables the hook. Loaded from SQLERY_PARTITION_ARCHIVE_HOOK.
            'PARTITION_ARCHIVE_HOOK': None,

            # How often the partition maintenance task runs (minutes).
            # Must be <= 43200 (one month in minutes) so maintenance never
            # runs less frequently than the partition interval.  Default: 1440
            # (once per day).
            # Loaded from SQLERY_PARTITION_MAINTENANCE_INTERVAL_MINUTES.
            'PARTITION_MAINTENANCE_INTERVAL_MINUTES': 1440,

            # Scheduled jobs older than this many days in the staging table
            # are considered overdue for promotion or expiry. Must be less than
            # PARTITION_RETENTION * 30 (so the retention window covers the
            # threshold).  Default: 7 days.
            # Loaded from SQLERY_SCHEDULED_JOB_THRESHOLD_DAYS.
            'SCHEDULED_JOB_THRESHOLD_DAYS': 7,
        }

        # Load from environment variables
        self._load_from_env()

    def _load_from_env(self):
        """Load configuration from environment variables."""
        # import os  # moved to top-level

        # Map environment variables to config keys
        env_mappings = {
            'SQLERY_DATABASE_URL': 'DATABASE_URL',
            'DJANGO_SQL_JOBS_MAX_WORKERS': 'MAX_WORKERS_PER_NODE',
            'DJANGO_SQL_JOBS_ENABLE_DAEMON': 'ENABLE_DAEMON',
            'DJANGO_SQL_JOBS_CHECK_INTERVAL': 'DAEMON_CHECK_INTERVAL',
            'SQLERY_POOL_SIZE': 'POOL_SIZE',
            'SQLERY_MAX_OVERFLOW': 'MAX_OVERFLOW',
            'SQLERY_POOL_TIMEOUT': 'POOL_TIMEOUT',
            'SQLERY_POOL_RECYCLE': 'POOL_RECYCLE',
            'SQLERY_SCHEDULER_JITTER_SECONDS': 'scheduler_jitter_seconds',
        }

        for env_key, config_key in env_mappings.items():
            env_value = os.getenv(env_key)

            if env_value is not None:
                # Type conversion
                if config_key in ['MAX_WORKERS_PER_NODE', 'DAEMON_CHECK_INTERVAL',
                                   'POOL_SIZE', 'MAX_OVERFLOW', 'POOL_TIMEOUT', 'POOL_RECYCLE']:
                    env_value = int(env_value)
                elif config_key == 'scheduler_jitter_seconds':
                    # CRON-03: jitter is a fractional-second delay, parse as float.
                    env_value = float(env_value)
                elif config_key == 'ENABLE_DAEMON':
                    env_value = env_value.lower() in ('true', '1', 'yes')

                self._config[config_key] = env_value

        # SEC-04: comma-separated allowlist. Strip whitespace, drop empties.
        # Absent env var leaves the default None (BC: allow all).
        raw_allowed = os.getenv("SQLERY_ALLOWED_TASK_MODULES")
        if raw_allowed is not None:
            parsed = [item.strip() for item in raw_allowed.split(",") if item.strip()]
            self._config["ALLOWED_TASK_MODULES"] = parsed if parsed else None

        # Comma-separated IP allowlist. Sentinel "*" disables the check.
        raw_ips = os.getenv("SQLERY_INTERNAL_ALLOWED_IPS")
        if raw_ips is not None:
            parsed_ips = [item.strip() for item in raw_ips.split(",") if item.strip()]
            self._config["INTERNAL_ALLOWED_IPS"] = parsed_ips

        # ---------------------------------------------------------------
        # Partition configuration env-var loading.
        # ---------------------------------------------------------------
        raw_interval = os.getenv("SQLERY_PARTITION_INTERVAL")
        if raw_interval is not None:
            self._config["PARTITION_INTERVAL"] = raw_interval.strip()

        raw_premake = os.getenv("SQLERY_PARTITION_PREMAKE")
        if raw_premake is not None:
            self._config["PARTITION_PREMAKE"] = int(raw_premake)

        raw_retention = os.getenv("SQLERY_PARTITION_RETENTION")
        if raw_retention is not None:
            self._config["PARTITION_RETENTION"] = int(raw_retention)

        raw_hook = os.getenv("SQLERY_PARTITION_ARCHIVE_HOOK")
        if raw_hook is not None:
            self._config["PARTITION_ARCHIVE_HOOK"] = raw_hook.strip() or None

        raw_maint = os.getenv("SQLERY_PARTITION_MAINTENANCE_INTERVAL_MINUTES")
        if raw_maint is not None:
            self._config["PARTITION_MAINTENANCE_INTERVAL_MINUTES"] = int(raw_maint)

        raw_threshold = os.getenv("SQLERY_SCHEDULED_JOB_THRESHOLD_DAYS")
        if raw_threshold is not None:
            self._config["SCHEDULED_JOB_THRESHOLD_DAYS"] = int(raw_threshold)

        # Validate partition invariants after loading all values.
        self._validate_partition_config()

    # Partition config keys — re-validate when any of these is mutated via set().
    _PARTITION_KEYS = frozenset(
        {
            "PARTITION_INTERVAL",
            "PARTITION_PREMAKE",
            "PARTITION_RETENTION",
            "PARTITION_MAINTENANCE_INTERVAL_MINUTES",
            "SCHEDULED_JOB_THRESHOLD_DAYS",
        }
    )

    def _validate_partition_config(self):
        """Validate partition configuration invariants.

        Raises:
            ValueError: If any invariant is violated.
        """
        interval = self._config.get("PARTITION_INTERVAL", "monthly")
        premake = self._config.get("PARTITION_PREMAKE", 3)
        retention = self._config.get("PARTITION_RETENTION", 24)
        maint_mins = self._config.get("PARTITION_MAINTENANCE_INTERVAL_MINUTES", 1440)
        threshold_days = self._config.get("SCHEDULED_JOB_THRESHOLD_DAYS", 7)

        # PARTITION_INTERVAL must be a recognised granularity.
        valid_intervals = {"monthly", "weekly"}
        if interval not in valid_intervals:
            raise ValueError(
                f"PARTITION_INTERVAL must be one of {valid_intervals!r}, got {interval!r}"
            )

        # PARTITION_PREMAKE must be >= 1.
        if premake < 1:
            raise ValueError(f"PARTITION_PREMAKE must be >= 1, got {premake!r}")

        # PARTITION_RETENTION must be >= 1 month.
        if retention < 1:
            raise ValueError(f"PARTITION_RETENTION must be >= 1, got {retention!r}")

        # PARTITION_MAINTENANCE_INTERVAL_MINUTES must be <= 43200 (30 days).
        # This ensures maintenance never runs less frequently than one month.
        max_maint_mins = 43200
        if maint_mins < 1 or maint_mins > max_maint_mins:
            raise ValueError(
                f"PARTITION_MAINTENANCE_INTERVAL_MINUTES must be in [1, {max_maint_mins}],"
                f" got {maint_mins!r}"
            )

        # SCHEDULED_JOB_THRESHOLD_DAYS must be at least 1.
        if threshold_days < 1:
            raise ValueError(
                f"SCHEDULED_JOB_THRESHOLD_DAYS must be >= 1, got {threshold_days!r}"
            )

        # Retention (months) must exceed threshold (days) when converted to months.
        # retention_days = retention * 30; retention_days must be > threshold_days.
        retention_days = retention * 30
        if retention_days <= threshold_days:
            raise ValueError(
                f"PARTITION_RETENTION ({retention} months = {retention_days} days) must be"
                f" strictly greater than SCHEDULED_JOB_THRESHOLD_DAYS ({threshold_days} days)"
            )

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value."""
        return self._config.get(key, default)

    def set(self, key: str, value: Any):
        """Set configuration value.

        Re-validates partition invariants whenever a partition-related key is
        updated so that misconfigurations are caught at write time rather than
        at maintenance time.
        """
        self._config[key] = value
        if key in self._PARTITION_KEYS:
            self._validate_partition_config()

    def all(self) -> dict:
        """Get all configuration values."""
        return dict(self._config)
