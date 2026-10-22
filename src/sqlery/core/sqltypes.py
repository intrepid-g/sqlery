"""Custom SQLAlchemy column types for sqlery."""

from datetime import datetime, UTC

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC datetime column that works on SQLite and PostgreSQL.

    SQLite returns naive datetimes and recent sqlmodel releases (>= 0.0.48) reject naive
    values on bind. This type normalises values to UTC on the way in (naive values are
    assumed to already be UTC) and attaches ``UTC`` on the way out, so application code
    only ever sees timezone-aware datetimes.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        """Convert to an aware UTC datetime before binding."""
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        """Attach UTC to naive values returned by the driver."""
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
