"""Party: Config. The operator's two liveness knobs must agree with each other."""

HEARTBEAT_INTERVAL_DEFAULT = 5  # WORKER_HEARTBEAT_INTERVAL, seconds
ALIVE_TIMEOUT_DEFAULT = 30  # WORKER_ALIVE_TIMEOUT, seconds


def heartbeat_fits_alive_timeout(interval: float, alive_timeout: float) -> bool:
    """Clause CFG-1: a worker must beat strictly more often than it may be silent.

    ``0`` is allowed: it means "beat on every poll".
    """
    return 0 <= interval < alive_timeout


def misconfiguration(interval: float, alive_timeout: float) -> str | None:
    """Clause CFG-2: the operator-facing complaint when CFG-1 is broken, else None."""
    if heartbeat_fits_alive_timeout(interval, alive_timeout):
        return None
    return (
        f"WORKER_HEARTBEAT_INTERVAL ({interval}s) must be below WORKER_ALIVE_TIMEOUT "
        f"({alive_timeout}s): otherwise a healthy worker is reported dead between beats."
    )
