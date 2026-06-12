"""Manual intervention logic — diagnose system state and reconcile DB with OS reality.

Three public functions:

  diagnose_system_health()       — read-only check. Returns list of problems.
                                   Empty list = system is healthy, intervention
                                   MUST be rejected.

  do_manual_intervention()       — runs inside the daemon process. Can check OS
                                   state, kill processes, and spawn replacement
                                   workers. Calls diagnose_system_health() first;
                                   if no problems found, returns immediately.

  do_manual_intervention_direct() — runs from the web server (API fallback).
                                   Can only fix DB state. Cannot spawn workers
                                   or kill processes reliably.
"""

import logging
import os
import signal
from datetime import timedelta

from django.db.models import F, Q
from django.utils import timezone

from sqlery.worker_pool import ensure_worker_pool

from .deadlines import _pid_is_sqlery_worker, _find_overdue_deadlines, enforce_deadlines
from .models import DaemonLease, QueuedJob, Worker

logger = logging.getLogger(__name__)

# ── Thresholds ─────────────────────────────────────────────────────
#
# These define what constitutes a problem vs normal operation.

# A busy worker whose heartbeat is older than this is considered stuck.
STALE_HEARTBEAT_THRESHOLD_SECONDS = 120

# A worker whose heartbeat is older than this is considered dead
# (used by the direct/DB-only fallback that can't check the OS).
DEAD_HEARTBEAT_THRESHOLD_SECONDS = 60


# ── Scenarios that are NOT problems ────────────────────────────────
#
# The following states look alarming but are normal operation:
#
# 1. Jobs queued + workers busy + heartbeats fresh
#    → Workers are processing. Queue will drain.
#
# 2. Jobs queued + workers idle + jobs finished recently (<5 min)
#    → Workers just finished a batch. Next claim cycle will pick up.
#
# 3. No jobs queued, no jobs running, workers idle
#    → System is idle. Nothing to do.
#
# 4. Workers paused (paused_until set)
#    → Intentional. Unpause is a separate action, not intervention.
#
# 5. Running job within its timeout
#    → Normal execution. Even if it's been running for a while.
#
# 6. No workers registered at all AND no queued jobs
#    → System is off. That's fine — nothing to process.


def diagnose_system_health(*, check_os: bool = False) -> list[dict]:
    """Read-only diagnosis. Returns a list of detected problems.

    Each problem is a dict with:
      kind  — machine-readable problem type
      msg   — human-readable description
      data  — dict with details (worker IDs, job IDs, counts, etc.)

    An empty list means the system is healthy. Intervention MUST NOT
    proceed if this returns empty.

    Args:
        check_os: If True, verify worker PIDs via the OS and check
                  deadline files on the local filesystem. Only valid
                  when called from the same host as the workers (i.e.,
                  from the daemon process). When called from a web
                  container that doesn't share a PID namespace with
                  workers, this MUST be False — os.kill(pid, 0) would
                  check the wrong process or a nonexistent one.

    When check_os=False (the default, safe for any caller), diagnosis
    relies only on purported reality (DB): stale heartbeats, ghost
    jobs, and queue/worker counts. These are weaker signals — a stale
    heartbeat doesn't prove the worker is dead, only that it stopped
    writing to the DB — but they are the only signals available
    cross-container.
    """
    # from .models import QueuedJob, Worker  # moved to top-level

    problems = []
    now = timezone.now()

    # ── Load current state ─────────────────────────────────────

    db_workers = list(Worker.objects.filter(status__in=['idle', 'busy']))

    # ── OS-level checks (same-host only) ───────────────────────
    #
    # These require the caller to share a PID namespace and
    # filesystem with the worker processes. Invalid cross-container.

    if check_os:
        alive_pids = set()
        for w in db_workers:
            if _pid_is_sqlery_worker(w.pid):
                alive_pids.add(w.pid)

        # Check 1: Workers DB says are active but process is dead
        dead_db_workers = [w for w in db_workers if w.pid not in alive_pids]
        if dead_db_workers:
            problems.append({
                'kind': 'dead_workers',
                'msg': (
                    f'{len(dead_db_workers)} worker(s) marked active in DB '
                    f'but process is dead'
                ),
                'data': {
                    'worker_ids': [str(w.id) for w in dead_db_workers],
                    'pids': [w.pid for w in dead_db_workers],
                },
            })

        # Check 2 (OS variant): Busy workers alive but stale heartbeat
        stale_busy = [
            w for w in db_workers
            if w.status == 'busy'
            and w.pid in alive_pids
            and (now - w.last_heartbeat).total_seconds() > STALE_HEARTBEAT_THRESHOLD_SECONDS
        ]
        if stale_busy:
            problems.append({
                'kind': 'stale_busy_workers',
                'msg': (
                    f'{len(stale_busy)} worker(s) alive but heartbeat stale '
                    f'>{STALE_HEARTBEAT_THRESHOLD_SECONDS}s (likely stuck)'
                ),
                'data': {
                    'worker_ids': [str(w.id) for w in stale_busy],
                    'pids': [w.pid for w in stale_busy],
                },
            })

        # Check 4 (OS): Jobs past their deadline (filesystem)
        overdue = _find_overdue_deadlines()
        if overdue:
            problems.append({
                'kind': 'overdue_deadlines',
                'msg': f'{len(overdue)} job(s) past their deadline',
                'data': {
                    'job_ids': [d['job_id'] for d in overdue],
                },
            })

    # ── DB-only checks (safe from any container) ───────────────

    if not check_os:
        # Check 2 (DB variant): Busy workers with stale heartbeat.
        # Without OS access we can't confirm the process is alive or
        # dead — we only know the DB hasn't been updated. But a
        # heartbeat >120s stale is strong evidence of a problem
        # regardless of whether the process technically still exists.
        stale_busy = [
            w for w in db_workers
            if w.status == 'busy'
            and w.last_heartbeat
            and (now - w.last_heartbeat).total_seconds() > STALE_HEARTBEAT_THRESHOLD_SECONDS
        ]
        if stale_busy:
            problems.append({
                'kind': 'stale_busy_workers',
                'msg': (
                    f'{len(stale_busy)} worker(s) with heartbeat stale '
                    f'>{STALE_HEARTBEAT_THRESHOLD_SECONDS}s (likely stuck or dead)'
                ),
                'data': {
                    'worker_ids': [str(w.id) for w in stale_busy],
                    'pids': [w.pid for w in stale_busy],
                },
            })

    # ── Check 6: Daemon lease expired (daemon is down) ──────────
    # Pure DB check — works from any container.
    # DaemonLease.expires_at is renewed every daemon loop iteration.
    # If all leases are expired, the daemon (parent process) is not
    # running — meaning no supervision, no cleanup, no worker spawning.

    try:
        # from .models import DaemonLease  # moved to top-level
        leases = DaemonLease.objects.all()
        if leases.exists():
            all_expired = all(lease.expires_at < now for lease in leases)
            if all_expired:
                oldest_expiry = min(lease.expires_at for lease in leases)
                stale_seconds = int((now - oldest_expiry).total_seconds())
                problems.append({
                    'kind': 'daemon_down',
                    'msg': (
                        f'Daemon lease expired {stale_seconds}s ago — '
                        f'daemon process is not running (no supervision)'
                    ),
                    'data': {
                        'expired_at': oldest_expiry.isoformat(),
                        'stale_seconds': stale_seconds,
                    },
                })
    except Exception:
        pass  # DaemonLease table may not exist yet

    # ── Check 3: Ghost running jobs (no active worker owns them) ─
    # Pure DB check — works from any container.

    active_job_ids = set(
        w.current_job_id for w in db_workers
        if w.current_job_id is not None
    )
    ghost_jobs = QueuedJob.objects.filter(
        status='running'
    ).exclude(id__in=active_job_ids)
    ghost_count = ghost_jobs.count()
    if ghost_count:
        problems.append({
            'kind': 'ghost_running_jobs',
            'msg': (
                f'{ghost_count} ghost job(s) in running state '
                f'with no active worker'
            ),
            'data': {
                'job_ids': list(ghost_jobs.values_list('id', flat=True)[:20]),
                'count': ghost_count,
            },
        })

    # ── Check 5: Jobs queued but no workers at all ─────────────
    # Pure DB check — works from any container.

    _due_filter = Q(scheduled_at__isnull=True) | Q(scheduled_at__lte=now)
    queued_count = QueuedJob.objects.filter(status='queued').filter(_due_filter).count()
    if queued_count > 0 and not db_workers:
        problems.append({
            'kind': 'no_workers',
            'msg': (
                f'{queued_count} job(s) queued but no active workers'
            ),
            'data': {
                'queued_count': queued_count,
            },
        })

    # ── Check 7: Running jobs past their timeout ───────────────
    # Pure DB check — works from any container.
    # If a job has exceeded started_at + timeout_seconds, it means
    # the worker's in-process SIGALRM failed AND the daemon's
    # deadline enforcement failed. Both supervision layers are broken.

    overdue_jobs = QueuedJob.objects.filter(
        status='running',
        timeout_seconds__isnull=False,
        started_at__isnull=False,
    )
    timed_out_jobs = []
    for job in overdue_jobs:
        if job.started_at + timedelta(seconds=job.timeout_seconds) < now:
            timed_out_jobs.append(job)
    if timed_out_jobs:
        problems.append({
            'kind': 'timed_out_jobs',
            'msg': (
                f'{len(timed_out_jobs)} running job(s) exceeded their timeout '
                f'(both worker SIGALRM and daemon watchdog failed)'
            ),
            'data': {
                'job_ids': [j.id for j in timed_out_jobs[:20]],
                'count': len(timed_out_jobs),
            },
        })

    return problems


def do_manual_intervention(payload: dict | None = None) -> dict:
    """Diagnose system state and take all necessary recovery actions.

    Calls diagnose_system_health() first. If no problems found,
    returns immediately without touching anything.

    The key principle: check actual reality (OS) first,
    then reconcile purported reality (DB) to match.
    """
    # from .models import QueuedJob, Worker  # moved to top-level
    # from sqlery.worker_pool import ensure_worker_pool  # moved to top-level

    # ── Gate: refuse to act if system is healthy ───────────────
    # check_os=True because we're in the daemon — same host as workers.

    problems = diagnose_system_health(check_os=True)
    if not problems:
        return {
            'diagnosed': ['No issues found — system appears healthy'],
            'actions_taken': [],
            'workers_killed': 0,
            'workers_spawned': 0,
            'jobs_failed': 0,
            'stale_workers_cleaned': 0,
        }

    result = {
        'diagnosed': [p['msg'] for p in problems],
        'actions_taken': [],
        'workers_killed': 0,
        'workers_spawned': 0,
        'jobs_failed': 0,
        'stale_workers_cleaned': 0,
    }
    now = timezone.now()

    # ── Build lookup structures from diagnosis ─────────────────

    # Re-fetch workers for modification (diagnosis was read-only)
    db_workers = list(Worker.objects.filter(status__in=['idle', 'busy']))
    alive_pids = set()
    for w in db_workers:
        if _pid_is_sqlery_worker(w.pid):
            alive_pids.add(w.pid)

    dead_db_workers = [w for w in db_workers if w.pid not in alive_pids]
    stale_busy = [
        w for w in db_workers
        if w.status == 'busy'
        and w.pid in alive_pids
        and (now - w.last_heartbeat).total_seconds() > STALE_HEARTBEAT_THRESHOLD_SECONDS
    ]
    active_job_ids = set(
        w.current_job_id for w in db_workers
        if w.current_job_id is not None
    )
    ghost_jobs = QueuedJob.objects.filter(
        status='running'
    ).exclude(id__in=active_job_ids)

    # ── Reconcile DB with reality ──────────────────────────────

    # Mark dead-process workers as dead, fail their jobs
    for w in dead_db_workers:
        if w.current_job_id:
            QueuedJob.objects.filter(
                id=w.current_job_id, status='running'
            ).update(
                status='failed',
                error='Auto-recovery: worker process confirmed dead (PID check)',
                termination_reason='daemon_intervention',
                finished_at=now,
                version=F('version') + 1,
            )
            result['jobs_failed'] += 1
        w.status = 'dead'
        # Old: w.current_job = None
        w.current_job_id = None
        # Old: w.save(update_fields=['status', 'current_job'])
        w.save(update_fields=['status', 'current_job_id'])
        result['stale_workers_cleaned'] += 1

    # Kill stuck workers (alive but stale heartbeat).
    # Non-blocking: send SIGTERM now, update DB immediately.
    for w in stale_busy:
        try:
            os.kill(w.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        if w.current_job_id:
            QueuedJob.objects.filter(
                id=w.current_job_id, status='running'
            ).update(
                status='failed',
                error='Auto-recovery: worker killed (stuck, stale heartbeat)',
                termination_reason='daemon_intervention',
                finished_at=now,
                version=F('version') + 1,
            )
            result['jobs_failed'] += 1
        w.status = 'dead'
        # Old: w.current_job = None
        w.current_job_id = None
        # Old: w.save(update_fields=['status', 'current_job'])
        w.save(update_fields=['status', 'current_job_id'])
        result['workers_killed'] += 1

    # Fail ghost jobs
    ghost_count = ghost_jobs.update(
        status='failed',
        error='Auto-recovery: ghost job with no active worker',
        termination_reason='daemon_intervention',
        finished_at=now,
        version=F('version') + 1,
    )
    result['jobs_failed'] += ghost_count

    # Enforce overdue deadlines (non-blocking)
    result['jobs_failed'] += enforce_deadlines()

    # Ensure workers exist
    pool_status = ensure_worker_pool()
    result['workers_spawned'] = pool_status.get('spawned', 0)

    # Summarize
    result['actions_taken'] = [
        a for a in [
            f'Cleaned {result["stale_workers_cleaned"]} stale worker(s)' if result['stale_workers_cleaned'] else None,
            f'Killed {result["workers_killed"]} stuck worker(s)' if result['workers_killed'] else None,
            f'Failed {result["jobs_failed"]} orphaned/stuck job(s)' if result['jobs_failed'] else None,
            f'Spawned {result["workers_spawned"]} replacement worker(s)' if result['workers_spawned'] else None,
        ] if a
    ]

    logger.info(f"Manual intervention completed: {result['actions_taken']}")
    return result


def do_manual_intervention_direct() -> dict:
    """Fallback: run intervention logic directly (no daemon).

    This handles the case where the daemon is dead but the web
    server is alive. It can fix DB state but cannot spawn workers
    or kill processes reliably.
    """
    # from .models import QueuedJob, Worker  # moved to top-level

    now = timezone.now()
    result = {
        'diagnosed': [],
        'actions_taken': [],
        'jobs_failed': 0,
        'stale_workers_cleaned': 0,
        'note': 'Ran without daemon — cannot spawn workers. Restart daemon manually.',
    }

    # Mark all stale workers as dead
    stale_threshold = now - timedelta(seconds=DEAD_HEARTBEAT_THRESHOLD_SECONDS)
    stale = Worker.objects.filter(
        status__in=['idle', 'busy'],
        last_heartbeat__lt=stale_threshold,
    )
    for w in stale:
        if w.current_job_id:
            QueuedJob.objects.filter(
                id=w.current_job_id, status='running'
            ).update(
                status='failed',
                error='Direct intervention: worker process presumed dead',
                termination_reason='direct_intervention',
                finished_at=now,
                version=F('version') + 1,
            )
            result['jobs_failed'] += 1
        result['stale_workers_cleaned'] += 1

    # Old: stale.update(status='dead', current_job=None)
    stale.update(status='dead', current_job_id=None)

    # Fail ghost running jobs
    active_job_ids = Worker.objects.filter(
        status__in=['idle', 'busy']
    ).exclude(
        current_job__isnull=True
    ).values_list('current_job_id', flat=True)

    ghost_count = QueuedJob.objects.filter(
        status='running'
    ).exclude(
        id__in=active_job_ids
    ).update(
        status='failed',
        error='Direct intervention: ghost job with no active worker',
        termination_reason='direct_intervention',
        finished_at=now,
        version=F('version') + 1,
    )
    result['jobs_failed'] += ghost_count

    if result['jobs_failed'] or result['stale_workers_cleaned']:
        result['diagnosed'].append('Cleaned stale state from DB')
        result['actions_taken'].append(
            f'Failed {result["jobs_failed"]} job(s), '
            f'cleaned {result["stale_workers_cleaned"]} worker(s)'
        )
    else:
        result['diagnosed'].append('No DB issues found — daemon may just need restart')

    logger.info(f"Direct intervention completed: {result}")
    return result
