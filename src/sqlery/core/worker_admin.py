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
