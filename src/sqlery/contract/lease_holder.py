"""Party: LeaseHolder (the daemon, or a bare worker holding a scheduler lease, #35)."""

from datetime import datetime

MAINTENANCE_INTERVAL_DEFAULT_MINUTES = 5
# The sweeps the lease holder owes, in order. The daemon runs the same ones itself.
SWEEP_STEPS = ("zombies_failed", "dead_workers", "partitions")


def interval_elapsed(last_run: datetime | None, interval_minutes: float, now: datetime) -> bool:
    """Clause LH-1: the first run is always due, then once per interval."""
    return last_run is None or (now - last_run).total_seconds() >= interval_minutes * 60


def sweep_due(
    owned_queues: set[str] | list[str],
    last_run: datetime | None,
    interval_minutes: float,
    now: datetime,
) -> bool:
    """Clause LH-2 (obligation): a worker sweeps only while it holds a lease, once per interval.

    A running daemon holds the leases, so a bare worker then stays out of the sweeps.
    """
    return bool(owned_queues) and interval_elapsed(last_run, interval_minutes, now)
