import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


def _configure_sqlite_connection(sender, connection, **kwargs):
    """Apply SQLite resilience settings on every new connection.

    Connected to the ``connection_created`` signal so that all Django
    processes (workers, daemon, web server, management commands) benefit
    from WAL mode and busy_timeout — not just worker subprocesses.
    """
    if connection.vendor != "sqlite":
        return

    from sqlery.django_sqlery.settings import get_setting

    cursor = connection.cursor()

    if get_setting("SQLITE_WAL_MODE", True):
        cursor.execute("PRAGMA journal_mode=WAL")

    busy_timeout_ms = get_setting("SQLITE_BUSY_TIMEOUT_MS", 5000)
    if busy_timeout_ms:
        cursor.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")


class DjangoSqleryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "sqlery.django_sqlery"
    verbose_name = "Tasks/Jobs"  # Section name in Django admin index

    # Backward compatibility: also works as 'sqlery' when using root package
    label = "sqlery"

    def ready(self):
        """Register connection_created signal for SQLite resilience."""
        from django.db.backends.signals import connection_created

        connection_created.connect(_configure_sqlite_connection)
        logger.debug("SQLery: registered connection_created signal for SQLite resilience")
