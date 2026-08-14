"""Pure decision functions for administrative worker operations (deletion eligibility).

No IO, no Django/SQLAlchemy imports — safe to unit-test in isolation.
"""

from datetime import datetime


# Gray area: how stale must a worker be before the user is allowed to delete it?
# Decision: 5 minutes (300s) without a heartbeat — well past any normal poll cycle.
# Forced by: no settings knob exists for an admin-delete threshold yet; revisit when: users ask to tune it per-deployment
# Alternatives: read DJANGO_SQL_JOBS setting, reuse the 24h dashboard cutoff, or the 60s "stalled" mark
def worker_delete_staleness_threshold_seconds() -> int:
    # I wish I had the time to: read this from DJANGO_SQL_JOBS so each deployment can tune it
    return 300


# Gray area: which workers may a user delete from the dashboard?
# Decision: a worker is deletable only when it is NOT beating — dead status, no heartbeat,
#   or a heartbeat older than the staleness threshold. A recently-beating worker is protected.
# Forced by: "a working (beating) worker cannot be deleted" is the only hard rule given; revisit when: we also want to verify the OS process is gone
# Alternatives: also signal/verify the PID is dead, or require status=='dead' exclusively
def is_worker_deletable(
    status: str | None,
    last_heartbeat: datetime | None,
    now: datetime,
    threshold_seconds: int,
) -> bool:
    # I wish I had the time to: also confirm the worker PID is no longer running before allowing delete
    if status == "dead":
        return True
    if last_heartbeat is None:
        return True
    age_seconds = (now - last_heartbeat).total_seconds()
    return age_seconds >= threshold_seconds


# Gray area: what makes a worker "alive"? The package used to answer this four ways —
#   no cutoff at all (dashboard counts), 24h (dashboard row list), 60s
#   (get_worker_heartbeats) and 30s (Worker.is_alive). A container destroyed with
#   ENABLE_DAEMON=False leaves its rows at status='idle' forever, and only the
#   daemon-only reaper (cleanup_dead_workers) ever writes status='dead' — so the
#   no-cutoff count reported dead workers as active and stuck-queue detection
#   called an empty queue healthy.
# Decision: ONE definition — a worker is alive when its status is not 'dead' AND its
#   last heartbeat is younger than WORKER_ALIVE_TIMEOUT (default 30s, the same knob
#   cleanup_dead_workers uses). Liveness is derived from the heartbeat at read time,
#   so it is correct with or without a reaper running.
# Forced by: heartbeats are written by the worker itself on every waitpid tick
#   (core/worker.py lease renewal), so a busy long-job worker still beats.
def is_worker_beating(
    status: str | None,
    last_heartbeat: datetime | None,
    now: datetime,
    timeout_seconds: int,
) -> bool:
    """Return True when the worker is genuinely alive (heartbeat within timeout)."""
    if status == "dead" or last_heartbeat is None:
        return False
    return (now - last_heartbeat).total_seconds() < timeout_seconds
