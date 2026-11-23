"""Party: QueueLock. A running ``allow_parallel=False`` job serializes its queue while it is live.

The lock is held by a running job (an open attempt), not by a row of its own. A holder that
can no longer run the job stops holding the lock on its own, without waiting for a sweep.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlery.contract.database import is_beating
from sqlery.contract.zombie_sweep import stale_threshold

# The parent force-kills a child at timeout + 60 s (worker.py two-layer timeout): a holder
# with no registered worker cannot still run after that.
UNREGISTERED_HOLDER_GRACE_SECONDS = 60


@dataclass(frozen=True)
class Holder:
    """A running job in the queue, as the lock sees it."""

    worker_status: str | None  # None: the attempt names no registered worker
    worker_last_heartbeat: datetime | None
    claimed_at: datetime
    timeout_seconds: float


def holder_is_live(holder: Holder, now: datetime, alive_timeout: float) -> bool:
    """Clause QL-1: a holder keeps the lock while it can still run the job.

    A registered worker holds it while it beat within the zombie sweep's stale threshold
    (a slow job is not a dead worker). A holder with no worker holds it until its job's
    timeout plus the kill grace have passed since the claim.
    """
    if holder.worker_status is not None or holder.worker_last_heartbeat is not None:
        return is_beating(
            holder.worker_status, holder.worker_last_heartbeat, now, stale_threshold(alive_timeout)
        )
    bound = holder.timeout_seconds + UNREGISTERED_HOLDER_GRACE_SECONDS
    return now < holder.claimed_at + timedelta(seconds=bound)


def must_wait(
    allow_parallel: bool, holders: list[Holder], now: datetime, alive_timeout: float
) -> bool:
    """Clause QL-2: a non-parallel job waits while its queue has a live holder.

    The wait is a skip at claim time: the job stays ready and later jobs are still claimed.
    """
    if allow_parallel:
        return False
    return any(holder_is_live(h, now, alive_timeout) for h in holders)
