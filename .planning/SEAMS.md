# SEAMS

Swappable decisions the protocol has created. OPEN = live shortcut; RESOLVED = closed, kept for history.

## Open seams

### `worker_delete_staleness_threshold_seconds()` — 2026-06-16 — Delete stale workers from the dashboard
- **File:** src/sqlery/core/worker_admin.py
- **Chose:** 5 minutes (300s) without a heartbeat before a worker is deletable
- **Alternative:** read the threshold from DJANGO_SQL_JOBS so each deployment can tune it
- **Forced by:** no settings knob exists for an admin-delete threshold yet
- **Revisit when:** users ask to tune it per-deployment
- **Status:** OPEN
- **Notes:**

### `is_worker_deletable()` — 2026-06-16 — Delete stale workers from the dashboard
- **File:** src/sqlery/core/worker_admin.py
- **Chose:** deletable when status is dead, heartbeat is missing, or heartbeat age ≥ threshold (heartbeat-only signal)
- **Alternative:** also signal/verify the OS process (PID) is actually gone before allowing delete
- **Forced by:** "a working (beating) worker cannot be deleted" is the only hard rule given
- **Revisit when:** we also want to verify the OS process is gone
- **Status:** OPEN
- **Notes:**

## Resolved seams

(None yet)
