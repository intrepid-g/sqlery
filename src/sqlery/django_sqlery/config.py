"""Django configuration implementation.

Wraps Django settings to implement the Config interface.
"""

from typing import Any
from django.conf import settings

from ..compat import Config


class DjangoConfig(Config):
    """Django settings wrapper for configuration.

    Reads from Django settings.DJANGO_SQL_JOBS dict.
    """

    def __init__(self):
        """Initialize Django config."""
        pass  # No-op, we read from settings directly each time

    @property
    def _settings(self):
        """Get settings dict, reading fresh from Django settings each time."""
        return getattr(settings, 'DJANGO_SQL_JOBS', {})

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value from Django settings."""
        return self._settings.get(key, default)

    def set(self, key: str, value: Any):
        """Set configuration value (no-op in Django mode).

        In Django mode, configuration is read-only from settings.py.
        This method does nothing but doesn't raise an error.
        """
        pass  # No-op in Django mode

    def all(self) -> dict:
        """Get all configuration values."""
        return dict(self._settings)
