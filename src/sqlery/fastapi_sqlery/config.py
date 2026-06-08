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

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value."""
        return self._config.get(key, default)

    def set(self, key: str, value: Any):
        """Set configuration value."""
        self._config[key] = value

    def all(self) -> dict:
        """Get all configuration values."""
        return dict(self._config)
