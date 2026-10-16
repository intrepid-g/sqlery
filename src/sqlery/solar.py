"""Solar schedules (Celery's ``solar``): run at sunrise / sunset / solar noon / dawn / dusk.

A solar schedule is stored in the normal ``cron_expression`` field, so it needs no schema
change and every scheduler, CLI and admin path that accepts a cron string accepts it:

    solar:<event> <latitude> <longitude> [<offset>]

    solar:sunrise 52.52 13.405            # Berlin sunrise
    solar:sunset 40.7128 -74.0060 -15m    # 15 minutes before New York sunset
    solar:solar_noon 0 0 +1h

``event`` is one of ``sunrise``, ``sunset``, ``solar_noon``, ``dawn``, ``dusk`` (dawn/dusk are
civil twilight, sun 6 degrees below the horizon). Latitude is positive north, longitude positive
east. ``offset`` is ``[+-]N`` with an optional unit ``s``/``m``/``h`` (default minutes).

Times are computed with the NOAA/Wikipedia sunrise equation in pure standard-library Python
(no new dependency); accuracy is about a minute, which is ample for scheduling. Events are
rounded down to the minute, like cron. At latitudes/dates where the event does not occur
(polar day/night) the next day it does occur is used.
"""

from __future__ import annotations

import dataclasses
import datetime
import math
import re

PREFIX = "solar:"
EVENTS = ("sunrise", "sunset", "solar_noon", "dawn", "dusk")
_HORIZON_DAYS = 400
_OFFSET_RE = re.compile(r"^([+-]?\d+(?:\.\d+)?)([smh]?)$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "": 60}
_J2000_DATE = datetime.date(2000, 1, 1)
_UTC = datetime.timezone.utc


class InvalidSolarSpec(ValueError):
    """The solar schedule string could not be parsed."""


def is_solar(expression: str | None) -> bool:
    return bool(expression) and expression.strip().lower().startswith(PREFIX)


@dataclasses.dataclass(frozen=True)
class SolarSchedule:
    event: str
    latitude: float
    longitude: float
    offset_seconds: float = 0.0

    def event_on(self, day: datetime.date) -> datetime.datetime | None:
        """UTC time of the event for the solar day nearest ``day`` (no offset), or None."""
        return _solar_event(day, self.latitude, self.longitude, self.event)

    def next_from(self, base: datetime.datetime) -> datetime.datetime:
        """First occurrence (offset applied, minute resolution) at or after ``base`` (UTC)."""
        if base.tzinfo is None:
            base = base.replace(tzinfo=_UTC)
        base = base.astimezone(_UTC)
        start = base.date() - datetime.timedelta(days=2)
        offset = datetime.timedelta(seconds=self.offset_seconds)
        # Consecutive days' events are ~24h apart, so the first one at/after ``base`` wins.
        for i in range(_HORIZON_DAYS + 3):
            ev = self.event_on(start + datetime.timedelta(days=i))
            if ev is None:
                continue
            ev = (ev + offset).replace(second=0, microsecond=0)
            if ev >= base:
                return ev
        raise InvalidSolarSpec(
            f"no {self.event} within {_HORIZON_DAYS} days at "
            f"lat={self.latitude} lon={self.longitude}"
        )


def parse_solar(expression: str) -> SolarSchedule:
    """Parse ``solar:<event> <lat> <lon> [<offset>]``."""
    if not is_solar(expression):
        raise InvalidSolarSpec(f'"{expression}" is not a solar schedule')
    parts = expression.strip()[len(PREFIX):].split()
    if len(parts) not in (3, 4):
        raise InvalidSolarSpec(
            f'"{expression}": expected "solar:<event> <latitude> <longitude> [<offset>]"'
        )
    event = parts[0].lower()
    if event not in EVENTS:
        raise InvalidSolarSpec(f'unknown solar event "{parts[0]}" (use one of {", ".join(EVENTS)})')
    try:
        lat, lon = float(parts[1]), float(parts[2])
    except ValueError:
        raise InvalidSolarSpec(f'"{expression}": latitude/longitude must be numbers') from None
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise InvalidSolarSpec("latitude must be in [-90, 90] and longitude in [-180, 180]")
    offset = 0.0
    if len(parts) == 4:
        m = _OFFSET_RE.match(parts[3].lower())
        if not m:
            raise InvalidSolarSpec(f'bad offset "{parts[3]}" (examples: -15m, +1h, 90)')
        offset = float(m.group(1)) * _UNIT_SECONDS[m.group(2)]
    return SolarSchedule(event, lat, lon, offset)


def solar_spec(event: str, latitude: float, longitude: float, offset_minutes: float = 0) -> str:
    """Build a validated spec string for ``cron_expression``."""
    spec = f"{PREFIX}{event} {latitude} {longitude}"
    if offset_minutes:
        spec += f" {offset_minutes:+g}m"
    parse_solar(spec)
    return spec


def _jd_to_datetime(jd: float) -> datetime.datetime:
    return datetime.datetime(1970, 1, 1, tzinfo=_UTC) + datetime.timedelta(days=jd - 2440587.5)


def _solar_event(day: datetime.date, lat: float, lon: float, event: str):
    """Sunrise equation; ``lon`` is degrees east (west negative)."""
    n = (day - _J2000_DATE).days
    j_star = n - lon / 360.0
    m = math.radians((357.5291 + 0.98560028 * j_star) % 360)
    c = 1.9148 * math.sin(m) + 0.02 * math.sin(2 * m) + 0.0003 * math.sin(3 * m)
    lam = math.radians((math.degrees(m) + c + 180 + 102.9372) % 360)
    j_transit = 2451545.0 + j_star + 0.0053 * math.sin(m) - 0.0069 * math.sin(2 * lam)
    if event == "solar_noon":
        return _jd_to_datetime(j_transit)
    sin_dec = math.sin(lam) * math.sin(math.radians(23.4397))
    dec = math.asin(sin_dec)
    altitude = math.radians(-6.0 if event in ("dawn", "dusk") else -0.833)
    phi = math.radians(lat)
    cos_h = (math.sin(altitude) - math.sin(phi) * sin_dec) / (math.cos(phi) * math.cos(dec))
    if cos_h < -1 or cos_h > 1:
        return None  # sun never crosses that altitude today (polar day/night)
    h = math.degrees(math.acos(cos_h))
    if event in ("sunrise", "dawn"):
        return _jd_to_datetime(j_transit - h / 360.0)
    return _jd_to_datetime(j_transit + h / 360.0)
