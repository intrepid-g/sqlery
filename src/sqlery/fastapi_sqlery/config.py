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

            # Opt-in PG LISTEN/NOTIFY dispatch (Phase 18 — D1/D8).
            # Default False = byte-identical polling behaviour when off.
            # This is the ONLY feature flag in the listen-notify milestone.
            # Loaded from SQLERY_PG_NOTIFY env var (true/1/yes → True).
            'SQLERY_PG_NOTIFY': False,

            # ---------------------------------------------------------------
            # Partition configuration (PostgreSQL range-partitioned tables).
            # These settings mirror the Django DEFAULTS in django_sqlery/settings.py
            # and are validated together on first access.
            # ---------------------------------------------------------------

            # CR-02: the stored keys + types below were mismatched with what the
            # consumers (backend.py reclaim path, daemon.py maintenance loop) request.
            # Consumers call get_config("SQLERY_PARTITION_RETENTION", "30 days") etc.
            # (SQLERY_-prefixed) and pass the value DIRECTLY to the partitioning SQL as
            # a PostgreSQL interval STRING (now() - %s::interval). The old keys here were
            # unprefixed AND stored ints (months), so every lookup missed and silently
            # fell back to the hardcoded default, and a "fixed" lookup would have passed
            # an int where a PG interval string is required. Reconciled below to MIRROR
            # the Django canonical names/types (django_sqlery/backend.py + core/daemon.py).

            # Old (key mismatch + wrong type — unprefixed, int months):
            # 'PARTITION_INTERVAL': 'monthly',
            # 'PARTITION_PREMAKE': 3,
            # 'PARTITION_RETENTION': 24,
            # 'PARTITION_ARCHIVE_HOOK': None,
            # 'PARTITION_MAINTENANCE_INTERVAL_MINUTES': 1440,
            # 'SCHEDULED_JOB_THRESHOLD_DAYS': 7,

            # Partition granularity as a PG interval string consumed directly by the
            # partition-maintenance SQL. Default '1 day' (mirrors daemon.py default).
            # Loaded from SQLERY_PARTITION_INTERVAL.
            'SQLERY_PARTITION_INTERVAL': '1 day',

            # Number of future partitions to create in advance. Must be >= 1.
            # Default: 7 (mirrors daemon.py default). Loaded from SQLERY_PARTITION_PREMAKE.
            'SQLERY_PARTITION_PREMAKE': 7,

            # Partition retention as a PG interval string: drop child partitions older
            # than this. Passed directly to reclaim_drained_partitions as now() - %s::interval.
            # Must exceed SQLERY_SCHEDULED_JOB_THRESHOLD_DAYS. Default: '30 days'
            # (mirrors daemon.py + Django backend defaults). Loaded from SQLERY_PARTITION_RETENTION.
            'SQLERY_PARTITION_RETENTION': '30 days',

            # Optional Python import path to a callable invoked before a
            # partition is dropped. Signature: hook(table_name, partition_name).
            # None disables the hook. Loaded from SQLERY_PARTITION_ARCHIVE_HOOK.
            'SQLERY_PARTITION_ARCHIVE_HOOK': None,

            # How often the partition maintenance task runs (minutes). Int minutes.
            # Must be <= the partition interval expressed in minutes (see
            # _validate_partition_config). Default: 1440 (once per day). NOTE: this key
            # is UNPREFIXED to match daemon.py get_config("PARTITION_MAINTENANCE_INTERVAL_MINUTES").
            # Loaded from SQLERY_PARTITION_MAINTENANCE_INTERVAL_MINUTES.
            'PARTITION_MAINTENANCE_INTERVAL_MINUTES': 1440,

            # Scheduled jobs older than this many days in the staging table
            # are considered overdue for promotion or expiry. Int days. Must be less
            # than the partition retention window. Default: 7 days.
            # Loaded from SQLERY_SCHEDULED_JOB_THRESHOLD_DAYS.
            'SQLERY_SCHEDULED_JOB_THRESHOLD_DAYS': 7,
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

        # Phase 18 (D1/D8): opt-in PG LISTEN/NOTIFY dispatch. Absent = False (no-op).
        raw_pg_notify = os.getenv("SQLERY_PG_NOTIFY")
        if raw_pg_notify is not None:
            self._config["SQLERY_PG_NOTIFY"] = raw_pg_notify.lower() in ("true", "1", "yes")

        # ---------------------------------------------------------------
        # Partition configuration env-var loading.
        # ---------------------------------------------------------------
        # CR-02: store under the SQLERY_-prefixed keys the consumers actually request,
        # and keep interval/retention as PG-interval STRINGS (not ints) so they pass
        # straight into the partition SQL. Old assignments wrote unprefixed keys and
        # coerced retention to int — see the commented-out lines below each block.
        raw_interval = os.getenv("SQLERY_PARTITION_INTERVAL")
        if raw_interval is not None:
            # Old: self._config["PARTITION_INTERVAL"] = raw_interval.strip()
            self._config["SQLERY_PARTITION_INTERVAL"] = raw_interval.strip()

        raw_premake = os.getenv("SQLERY_PARTITION_PREMAKE")
        if raw_premake is not None:
            # Old: self._config["PARTITION_PREMAKE"] = int(raw_premake)
            self._config["SQLERY_PARTITION_PREMAKE"] = int(raw_premake)

        raw_retention = os.getenv("SQLERY_PARTITION_RETENTION")
        if raw_retention is not None:
            # Old: self._config["PARTITION_RETENTION"] = int(raw_retention)
            # Retention is a PG interval string (e.g. '30 days'), not an int.
            self._config["SQLERY_PARTITION_RETENTION"] = raw_retention.strip()

        raw_hook = os.getenv("SQLERY_PARTITION_ARCHIVE_HOOK")
        if raw_hook is not None:
            # Old: self._config["PARTITION_ARCHIVE_HOOK"] = raw_hook.strip() or None
            self._config["SQLERY_PARTITION_ARCHIVE_HOOK"] = raw_hook.strip() or None

        raw_maint = os.getenv("SQLERY_PARTITION_MAINTENANCE_INTERVAL_MINUTES")
        if raw_maint is not None:
            # Unprefixed key — matches daemon.py get_config lookup.
            self._config["PARTITION_MAINTENANCE_INTERVAL_MINUTES"] = int(raw_maint)

        raw_threshold = os.getenv("SQLERY_SCHEDULED_JOB_THRESHOLD_DAYS")
        if raw_threshold is not None:
            # Old: self._config["SCHEDULED_JOB_THRESHOLD_DAYS"] = int(raw_threshold)
            self._config["SQLERY_SCHEDULED_JOB_THRESHOLD_DAYS"] = int(raw_threshold)

        # Validate partition invariants after loading all values.
        self._validate_partition_config()

    # Partition config keys — re-validate when any of these is mutated via set().
    # CR-02: reconciled to the SQLERY_-prefixed names the consumers request (and the
    # unprefixed maintenance-interval key the daemon reads).
    # Old (unprefixed, mismatched with consumers):
    # _PARTITION_KEYS = frozenset({"PARTITION_INTERVAL", "PARTITION_PREMAKE",
    #     "PARTITION_RETENTION", "PARTITION_MAINTENANCE_INTERVAL_MINUTES",
    #     "SCHEDULED_JOB_THRESHOLD_DAYS"})
    _PARTITION_KEYS = frozenset(
        {
            "SQLERY_PARTITION_INTERVAL",
            "SQLERY_PARTITION_PREMAKE",
            "SQLERY_PARTITION_RETENTION",
            "PARTITION_MAINTENANCE_INTERVAL_MINUTES",
            "SQLERY_SCHEDULED_JOB_THRESHOLD_DAYS",
        }
    )

    @staticmethod
    def _interval_to_minutes(interval_str: str) -> int | None:
        """Parse a PG interval string like '1 day'/'7 days'/'2 hours' to minutes.

        Returns None when the format is unrecognised (fail-safe: skip the bound).
        Mirrors core/daemon.py._validate_partition_maintenance_interval parsing.
        """
        import re

        m = re.match(r"(\d+)\s*(day|hour|minute)", interval_str, re.IGNORECASE)
        if m is None:
            return None
        count, unit = int(m.group(1)), m.group(2).lower()
        return count * {"day": 1440, "hour": 60, "minute": 1}[unit]

    @staticmethod
    def _interval_to_days(interval_str: str) -> float | None:
        """Parse a PG interval string to a day count (float). None if unrecognised."""
        import re

        m = re.match(r"(\d+)\s*(day|hour|minute)", interval_str, re.IGNORECASE)
        if m is None:
            return None
        count, unit = int(m.group(1)), m.group(2).lower()
        return count * {"day": 1.0, "hour": 1 / 24, "minute": 1 / 1440}[unit]

    def _validate_partition_config(self):
        """Validate partition configuration invariants.

        Raises:
            ValueError: If any invariant is violated.
        """
        # CR-02: read the reconciled SQLERY_-prefixed keys with the reconciled types.
        # interval/retention are PG interval STRINGS now (not 'monthly'/'weekly' or int).
        # Old (unprefixed keys / int retention / granularity strings):
        # interval = self._config.get("PARTITION_INTERVAL", "monthly")
        # premake = self._config.get("PARTITION_PREMAKE", 3)
        # retention = self._config.get("PARTITION_RETENTION", 24)
        # threshold_days = self._config.get("SCHEDULED_JOB_THRESHOLD_DAYS", 7)
        interval = self._config.get("SQLERY_PARTITION_INTERVAL", "1 day")
        premake = self._config.get("SQLERY_PARTITION_PREMAKE", 7)
        retention = self._config.get("SQLERY_PARTITION_RETENTION", "30 days")
        maint_mins = self._config.get("PARTITION_MAINTENANCE_INTERVAL_MINUTES", 1440)
        threshold_days = self._config.get("SQLERY_SCHEDULED_JOB_THRESHOLD_DAYS", 7)

        # PARTITION_INTERVAL must be a parseable PG interval string.
        interval_minutes = self._interval_to_minutes(interval)
        if interval_minutes is None or interval_minutes < 1:
            raise ValueError(
                "SQLERY_PARTITION_INTERVAL must be a PG interval string like "
                f"'1 day'/'7 days'/'2 hours', got {interval!r}"
            )

        # PARTITION_PREMAKE must be >= 1.
        if premake < 1:
            raise ValueError(f"SQLERY_PARTITION_PREMAKE must be >= 1, got {premake!r}")

        # PARTITION_RETENTION must be a parseable PG interval string of >= 1 day.
        retention_days = self._interval_to_days(retention)
        if retention_days is None or retention_days < 1:
            raise ValueError(
                "SQLERY_PARTITION_RETENTION must be a PG interval string like "
                f"'30 days', got {retention!r}"
            )

        # IN-02: maintenance interval must not exceed the partition interval in minutes
        # (mirrors core/daemon.py._validate_partition_maintenance_interval). The old
        # bound was a fixed 43200 (30 days) regardless of granularity, so a '1 day'
        # interval would accept a 30-day maintenance cadence and never provision ahead.
        # Old: max_maint_mins = 43200
        max_maint_mins = interval_minutes
        if maint_mins < 1 or maint_mins > max_maint_mins:
            raise ValueError(
                f"PARTITION_MAINTENANCE_INTERVAL_MINUTES must be in [1, {max_maint_mins}]"
                f" for SQLERY_PARTITION_INTERVAL={interval!r}, got {maint_mins!r}"
            )

        # SCHEDULED_JOB_THRESHOLD_DAYS must be at least 1.
        if threshold_days < 1:
            raise ValueError(
                f"SQLERY_SCHEDULED_JOB_THRESHOLD_DAYS must be >= 1, got {threshold_days!r}"
            )

        # Retention (days) must strictly exceed the staging threshold (days).
        if retention_days <= threshold_days:
            raise ValueError(
                f"SQLERY_PARTITION_RETENTION ({retention!r} = {retention_days:.4g} days) must be"
                f" strictly greater than SQLERY_SCHEDULED_JOB_THRESHOLD_DAYS ({threshold_days} days)"
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
