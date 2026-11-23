"""Housekeeping a bare WorkerProcess runs when no daemon does it (#35).

The worker that holds a scheduler lease runs ``maintenance_tick`` at startup and then
every ``PARTITION_MAINTENANCE_INTERVAL_MINUTES``. A running daemon holds the leases, so
it stays the only one that does this work.
"""
import logging
from datetime import datetime, timezone

from ..compat import get_config
from ..contract import lease_holder
from . import history as history_core
from .daemon import DaemonManager, _nullable_cursor_cm, _partitioning
from .worker_pool import WorkerPoolManager

logger = logging.getLogger(__name__)


def is_due(last_run: datetime | None, interval_minutes: float, now: datetime) -> bool:
    """True on the first run, then once ``interval_minutes`` have passed."""
    # Old: inline check, now clause LH-1 (kept as a shim for callers)
    return lease_holder.interval_elapsed(last_run, interval_minutes, now)


def _raw_cursor(backend):
    cm = getattr(backend, "raw_cursor", None)
    return cm() if cm is not None else _nullable_cursor_cm(backend.get_raw_cursor())


def partition_tick(backend) -> tuple[int, int] | None:
    """Premake and reclaim partitions; ``(created, dropped)`` or None when not partitioned."""
    if _partitioning is None or not get_config("PARTITION_MAINTENANCE_ENABLED", True):
        return None
    with _raw_cursor(backend) as cur:
        if cur is None:
            return None
        made = history_core.ensure_partitions(
            cur,
            get_config("SQLERY_PARTITION_INTERVAL", "1 day"),
            get_config("SQLERY_PARTITION_PREMAKE", 7),
            _partitioning,
        )
        dropped = sum(
            history_core.reclaim_partitions(
                cur,
                get_config("SQLERY_PARTITION_RETENTION", "30 days"),
                get_config("SQLERY_PARTITION_ARCHIVE_HOOK", None),
                _partitioning,
            ).values()
        )
    return made, dropped


def maintenance_tick(backend, queues) -> dict:
    """Fail zombie jobs, mark dead workers, keep partitions. Each step is best-effort."""
    result: dict = {}
    steps = {  # keyed by the clause names in lease_holder.SWEEP_STEPS
        # concurrent_workers=True: a worker with a thread pool runs several jobs at once.
        "zombies_failed": lambda: DaemonManager._fail_zombie_running_jobs(
            backend, queue_names=queues, concurrent_workers=True
        ),
        "dead_workers": lambda: WorkerPoolManager(0, list(queues or []), backend).cleanup_dead_workers(),
        "partitions": lambda: partition_tick(backend),
    }
    for name in lease_holder.SWEEP_STEPS:
        step = steps[name]
        try:
            result[name] = step()
        except Exception as e:
            logger.error(f"Worker maintenance step {name} failed: {e}", exc_info=True)
            result[name] = "error"
    logger.info("Worker maintenance: " + " ".join(f"{k}={v}" for k, v in result.items()))
    return result


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
