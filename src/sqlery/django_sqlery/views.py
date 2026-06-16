"""Async views for internal worker trigger endpoint."""

import asyncio
import functools
import logging
import os
import sys
import tempfile
from datetime import timedelta
from io import StringIO

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.cache import caches
from django.core.management import call_command
from django.db.models import Count, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import QueuedJob, ScheduledTask, Worker
from .settings import get_setting
from .signature import verify_signature
from .subprocess_executor import get_manage_py_path

logger = logging.getLogger(__name__)


def _job_display_name(j) -> str:
    """Return a human-readable display name for a queued job.

    Priority: job_name field → scheduled task name → last segment of task_path → full task_path → fallback.
    Never returns an empty string or a bare status word like 'failed'.
    """
    if j.job_name:
        return j.job_name
    if j.scheduled_task and j.scheduled_task.name:
        return j.scheduled_task.name
    if j.task_path:
        short = j.task_path.rsplit('.', 1)[-1]
        # Guard against task_path ending with a status word or being unhelpfully short
        if short and short not in {'failed', 'success', 'queued', 'started', 'running'}:
            return short
        return j.task_path
    return f'job #{j.id}'


def _serialize_worker(worker, now) -> dict:
    """Convert a Worker model instance to a dashboard-ready dict."""
    worker_data = {
        'id': str(worker.id),
        'friendly_name': worker.friendly_name,
        'node_id': worker.node_id,
        'pid': worker.pid,
        'status': worker.status,
        'current_job_id': worker.current_job_id,
        'jobs_processed': worker.jobs_processed,
        'started_at': worker.started_at.isoformat() if worker.started_at else None,
        'last_heartbeat': worker.last_heartbeat.isoformat() if worker.last_heartbeat else None,
    }

    # Uptime + busy/idle breakdown (read from Worker row, tracked by parent process)
    if worker.started_at:
        uptime_seconds = (now - worker.started_at).total_seconds()
        # # Old: aggregate query per worker (fragile with fork-per-job PIDs)
        # busy_agg = QueuedJob.objects.filter(
        #     worker_pid=worker.pid,
        #     status__in=('success', 'failed'),
        #     finished_at__gte=worker.started_at,
        # ).aggregate(total=Sum('duration_seconds'))
        # busy_seconds = busy_agg['total'] or 0.0
        busy_seconds = worker.total_busy_seconds or 0.0
        # total_busy_seconds only updates on heartbeat, which doesn't fire
        # while the parent is blocked waiting for a child. Add the current
        # job's in-progress elapsed time so the % is accurate live.
        if worker.status == 'busy' and worker.current_job_id:
            # try:
            #     current_job = QueuedJob.objects.get(id=worker.current_job_id)
            #     if current_job.started_at:
            #         busy_seconds += (now - current_job.started_at).total_seconds()
            # except QueuedJob.DoesNotExist:
            #     pass
            # Old: if worker.current_job and worker.current_job.started_at:
            # current_job FK demoted to current_job_id (D4, Phase 15); fetch explicitly
            try:
                _cj = QueuedJob.objects.only("started_at").get(id=worker.current_job_id)
                if _cj.started_at:
                    busy_seconds += (now - _cj.started_at).total_seconds()
            except QueuedJob.DoesNotExist:
                pass
        worker_data['uptime_seconds'] = uptime_seconds
        worker_data['busy_seconds'] = busy_seconds
        worker_data['idle_seconds'] = max(uptime_seconds - busy_seconds, 0.0)
        worker_data['utilization_pct'] = round(busy_seconds / uptime_seconds * 100, 1) if uptime_seconds > 0 else 0.0
    else:
        worker_data['uptime_seconds'] = None
        worker_data['busy_seconds'] = None
        worker_data['idle_seconds'] = None
        worker_data['utilization_pct'] = None

    # Pause state
    worker_data['paused_until'] = worker.paused_until.isoformat() if worker.paused_until else None
    worker_data['is_paused'] = bool(worker.paused_until and worker.paused_until > now)

    if worker.last_heartbeat:
        heartbeat_age = (now - worker.last_heartbeat).total_seconds()
        worker_data['heartbeat_age_seconds'] = heartbeat_age
        worker_data['is_stalled'] = heartbeat_age > 60
    else:
        worker_data['heartbeat_age_seconds'] = None
        worker_data['is_stalled'] = True

    if worker.current_job_id:
        # try:
        #     # Re-fetch job to get latest status (avoids stale select_related)
        #     job = QueuedJob.objects.select_related('scheduled_task').get(id=worker.current_job_id)
        #     if job:
        #         worker_data['current_job'] = { ... }
        #         if job.status != 'running':
        #             worker_data['status'] = 'idle'
        #             worker_data['current_job'] = None
        #         elif job.started_at:
        #             elapsed = (now - job.started_at).total_seconds()
        #             worker_data['current_job']['elapsed_seconds'] = elapsed
        #             worker_data['current_job']['is_timeout'] = bool(...)
        #         else:
        #             worker_data['current_job']['elapsed_seconds'] = None
        #             worker_data['current_job']['is_timeout'] = False
        #     else:
        #         worker_data['current_job'] = None
        # except QueuedJob.DoesNotExist:
        #     worker_data['current_job'] = None
        #     worker_data['status'] = 'idle'
        # except Exception:
        #     worker_data['current_job'] = None
        # Old: job = worker.current_job  (FK demoted to current_job_id — D4, Phase 15)
        # Fetch current job explicitly using current_job_id
        try:
            job = QueuedJob.objects.select_related('scheduled_task').get(id=worker.current_job_id)
        except QueuedJob.DoesNotExist:
            job = None
        if job:
            worker_data['current_job'] = {
                'id': job.id,
                'task_path': job.task_path,
                'task_name': _job_display_name(job),
                'status': job.status,
                'started_at': job.started_at.isoformat() if job.started_at else None,
                'timeout_seconds': job.timeout_seconds,
            }
            if job.status != 'running':
                worker_data['status'] = 'idle'
                worker_data['current_job'] = None
            elif job.started_at:
                elapsed = (now - job.started_at).total_seconds()
                worker_data['current_job']['elapsed_seconds'] = elapsed
                worker_data['current_job']['is_timeout'] = bool(job.timeout_seconds and elapsed > job.timeout_seconds)
            else:
                worker_data['current_job']['elapsed_seconds'] = None
                worker_data['current_job']['is_timeout'] = False
        else:
            worker_data['current_job'] = None
    else:
        worker_data['current_job'] = None

    return worker_data


def _compute_health_warnings(
    now,
    *,
    precomputed_workers=None,
    precomputed_queued_count=None,
    precomputed_running_count=None,
    precomputed_running_jobs=None,
) -> list[dict]:
    """Compute current system health warnings with actionable metadata.

    Each warning is a dict with:
      type      — always 'warning'
      msg       — human-readable description
      time      — ISO timestamp (now)
      action    — dict or None:
                    label      — button text
                    kind       — 'stop_job' | 'unpause_workers' | None
                    job_id     — int (stop_job)
                    job_name   — str (stop_job)
                    worker_ids — list[str] (unpause_workers)

    Called by both the stats endpoint (for the health panel, polled every 3s)
    and the feed endpoint (for feed items on initial load).
    """
    # from .models import Worker  # moved to top-level
    warnings_list = []
    ts = now.isoformat()

    # active_workers = list(
    #     Worker.objects.filter(status__in=['idle', 'busy']).exclude(pid=0)
    #     .select_related('current_job')
    # )
    if precomputed_workers is not None:
        active_workers = precomputed_workers
    else:
        # Old: .select_related('current_job')  — FK demoted to current_job_id (D4, Phase 15)
        active_workers = list(
            Worker.objects.filter(status__in=['idle', 'busy']).exclude(pid=0)
        )
    busy_workers = [w for w in active_workers if w.status == 'busy']
    idle_workers = [w for w in active_workers if w.status == 'idle']

    # 1. Busy worker with stale heartbeat — likely dead/hung
    for w in busy_workers:
        if w.last_heartbeat:
            age = (now - w.last_heartbeat).total_seconds()
            if age > 120:
                age_str = f'{int(age)}s'
                job_info = f' on job #{w.current_job_id}' if w.current_job_id else ''
                job_name = None
                # Old: if w.current_job: job_name = _job_display_name(w.current_job)  (FK demoted)
                if w.current_job_id:
                    try:
                        _wj = QueuedJob.objects.only("job_name", "task_path", "scheduled_task_id").get(id=w.current_job_id)
                        job_name = _job_display_name(_wj)
                    except QueuedJob.DoesNotExist:
                        pass
                action = {
                    'label': f'Stop job #{w.current_job_id}',
                    'kind': 'stop_job',
                    'job_id': w.current_job_id,
                    'job_name': job_name,
                } if w.current_job_id else None
                warnings_list.append({
                    'type': 'warning',
                    'msg': f'Worker {w.friendly_name} last heartbeat {age_str} ago{job_info} — may be stalled',
                    'time': ts,
                    'action': action,
                })

    # from django.db.models import Q as _Q  # moved to top-level
    # Only count due jobs (not future-scheduled) for health warnings
    _due_filter = Q(scheduled_at__isnull=True) | Q(scheduled_at__lte=now)
    # queued_count = QueuedJob.objects.filter(status='queued').count()
    # queued_count = QueuedJob.objects.filter(status='queued').filter(_due_filter).count()
    # running_count = QueuedJob.objects.filter(status='running').count()
    if precomputed_queued_count is not None:
        queued_count = precomputed_queued_count
    else:
        queued_count = QueuedJob.objects.filter(status='queued').filter(_due_filter).count()
    if precomputed_running_count is not None:
        running_count = precomputed_running_count
    else:
        running_count = QueuedJob.objects.filter(status='running').count()

    # 2. Jobs queued, no active workers at all
    if queued_count > 0 and not active_workers:
        warnings_list.append({
            'type': 'warning',
            'msg': f'{queued_count} job(s) queued but no active workers — restart the worker daemon',
            'time': ts,
            'action': {
                'kind': 'manual_intervention',
                'label': 'Fix Now',
            },
        })

    # 3. Jobs queued, workers present but idle and nothing running
    #    A backed-up queue with a busy worker is normal — only warn when
    #    workers are idle AND no jobs have finished recently (5 min).
    elif queued_count > 0 and running_count == 0 and idle_workers:
        recent_completed = QueuedJob.objects.filter(
            status__in=['success', 'failed'],
            finished_at__gte=now - timedelta(minutes=5),
        ).exists()
        if not recent_completed:
            oldest = QueuedJob.objects.filter(status='queued').filter(_due_filter).order_by('created_at').first()
            wait = f'{int((now - oldest.created_at).total_seconds())}s' if oldest and oldest.created_at else '?'
            paused_workers = [w for w in idle_workers if w.paused_until and w.paused_until > now]
            if paused_workers:
                action = {
                    'label': f'Unpause {len(paused_workers)} worker(s)',
                    'kind': 'unpause_workers',
                    'worker_ids': [str(w.id) for w in paused_workers],
                }
                msg = f'{queued_count} job(s) waiting {wait} — all workers are paused'
            else:
                action = {
                    'kind': 'manual_intervention',
                    'label': 'Fix Now',
                }
                msg = f'{queued_count} job(s) waiting {wait} — workers idle but not picking up (check daemon)'
            warnings_list.append({'type': 'warning', 'msg': msg, 'time': ts, 'action': action})

    # 4. Running job exceeded its timeout
    # running_jobs = QueuedJob.objects.filter(status='running').exclude(timeout_seconds=None)
    if precomputed_running_jobs is not None:
        running_jobs = [j for j in precomputed_running_jobs if j.timeout_seconds is not None]
    else:
        running_jobs = QueuedJob.objects.filter(status='running').exclude(timeout_seconds=None)
    for job in running_jobs:
        if job.started_at and job.timeout_seconds:
            elapsed = (now - job.started_at).total_seconds()
            if elapsed > job.timeout_seconds:
                over = int(elapsed - job.timeout_seconds)
                name = _job_display_name(job)
                warnings_list.append({
                    'type': 'warning',
                    'msg': f'Job #{job.id} ({name}) running {int(elapsed)}s — {over}s over its {job.timeout_seconds}s timeout',
                    'time': ts,
                    'action': {
                        'label': f'Stop job #{job.id}',
                        'kind': 'stop_job',
                        'job_id': job.id,
                        'job_name': name,
                    },
                })

    return warnings_list


def _get_health_warnings(now, **kwargs) -> list[dict]:
    """Thin wrapper with 2s cache — catches errors so a health-check failure never breaks the stats endpoint.

    When called without precomputed data (e.g. from activity feed), uses a 2-second cache
    to avoid re-running the same queries that dashboard_stats just executed.
    """
    # from django.core.cache import caches  # moved to top-level
    # If no precomputed data passed, try cache first
    if not kwargs:
        cached = caches['default'].get('sqlery_health_warnings')
        if cached is not None:
            return cached
    try:
        result = _compute_health_warnings(now, **kwargs)
        caches['default'].set('sqlery_health_warnings', result, timeout=2)
        return result
    except Exception:
        logger.exception("health_warnings computation failed")
        return []


def staff_required_json(view_func):
    """Like @staff_member_required but returns 403 JSON instead of redirect.

    Standard staff_member_required returns an HTML redirect to the login page,
    which breaks fetch().json() calls from the dashboard JavaScript.
    """
    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.user.is_active and request.user.is_staff:
            return view_func(request, *args, **kwargs)
        return JsonResponse({"error": "Authentication required"}, status=403)
    return wrapper


@csrf_exempt
@require_POST
async def internal_worker(request):
    """Internal worker trigger endpoint (ASGI-compatible async view).

    This endpoint:
    1. Verifies HMAC signature to prevent unauthorized access
    2. Spawns subprocess to run scheduler + workers
    3. Returns immediately without waiting for completion

    Security:
    - HMAC-SHA256 signature with 5-second expiry
    - Only accepts POST requests
    - Requires X-Signature and X-Timestamp headers

    Returns:
        200: Worker triggered successfully
        403: Invalid or missing signature
        405: Method not allowed (non-POST)
    """
    # Defense-in-depth: reject non-allowlisted source IPs. Use the real socket
    # peer (REMOTE_ADDR), never X-Forwarded-For (attacker-controllable).
    from sqlery.core.triggers import is_ip_allowed
    remote_addr = request.META.get("REMOTE_ADDR")
    if not is_ip_allowed(remote_addr):
        logger.warning(f"Internal worker request from disallowed IP: {remote_addr!r}")
        return JsonResponse({"error": "Forbidden source address"}, status=403)

    # Get signature headers
    signature = request.headers.get("X-Signature")
    timestamp = request.headers.get("X-Timestamp")

    if not signature or not timestamp:
        logger.warning("Missing signature headers in internal worker request")
        return JsonResponse(
            {"error": "Missing signature headers"}, status=403
        )

    # Verify signature
    secret = get_setting("INTERNAL_SECRET")
    if not secret:
        logger.error("INTERNAL_SECRET not configured")
        return JsonResponse(
            {"error": "Server misconfiguration"}, status=500
        )

    max_age = get_setting("SIGNATURE_MAX_AGE", 5)
    if not verify_signature(signature, timestamp, secret, max_age):
        return JsonResponse(
            {"error": "Invalid signature"}, status=403
        )

    # Spawn subprocess to run workers (non-blocking, prevents event loop blocking)
    try:
        await spawn_worker_subprocess()
        logger.info("Worker subprocess spawned successfully")
        return JsonResponse({"status": "ok", "message": "Worker triggered"})

    except Exception as e:
        logger.error(f"Failed to spawn worker subprocess: {e}")
        return JsonResponse(
            {"error": "Failed to trigger worker"}, status=500
        )


async def spawn_worker_subprocess():
    """Spawn subprocess to run scheduler and workers.

    Uses asyncio.create_subprocess_exec for true async subprocess spawning.
    Process runs in detached mode to prevent zombies.

    The subprocess runs: python /path/to/manage.py run_jobs --once
    Uses absolute path to manage.py to work regardless of CWD.
    """
    # import sys  # moved to top-level
    # from .subprocess_executor import get_manage_py_path  # moved to top-level

    # Get absolute path to manage.py (prevents CWD issues)
    manage_py = get_manage_py_path()

    # Create subprocess
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        manage_py,
        "run_jobs",
        "--once",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,  # Detach from parent, prevents zombies
    )

    logger.info(f"Spawned worker subprocess (PID: {proc.pid})")

    # Don't await - fire and forget
    # start_new_session=True ensures OS reaps the process
    # If we wanted to track completion, we'd use:
    # asyncio.create_task(proc.wait())


@csrf_exempt
async def health_check(request):
    """Simple health check endpoint for monitoring.

    Returns:
        200: Service is healthy
    """
    # from .models import QueuedJob  # moved to top-level

    # Check database connectivity
    try:
        queued_count = await asyncio.to_thread(
            lambda: QueuedJob.objects.filter(status="queued").count()
        )

        return JsonResponse({
            "status": "healthy",
            "queued_jobs": queued_count,
        })

    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return JsonResponse(
            {"status": "unhealthy", "error": str(e)}, status=500
        )


def _detect_and_fix_irregularities(now, workers_queryset):
    """Observe worker/job state for dashboard reporting only.

    This is a READ-ONLY check — it does NOT modify any state.
    All corrective actions are handled by the daemon:
    - _heartbeat_workers (SIGUSR1) keeps heartbeats fresh
    - _fail_zombie_running_jobs catches orphaned jobs
    - claim_job fails abandoned jobs when worker moves on
    - detect_and_fix_irregularities (worker_pool) is the last-resort safety net
    """
    # No-op: the daemon handles all corrections.
    # Return empty dict so callers don't break.
    return {}


@staff_required_json
def dashboard_stats(request):
    """Dashboard statistics API (returns JSON).

    Returns real-time stats for:
    - Job counts by status
    - Queue statistics
    - Scheduled task stats
    - Recent activity
    - Worker status (multi-worker mode)

    Auto-refreshed by dashboard every 3 seconds.
    Cached for 2 seconds (in-memory) to absorb concurrent polls.
    Rate-limited to 1 request per 5 seconds per session.
    """
    # from django.core.cache import caches  # moved to top-level
    # from django.db.models import Count, Q, Sum  # moved to top-level
    # from django.utils import timezone  # moved to top-level
    # from datetime import timedelta  # moved to top-level
    # from .models import ScheduledTask, QueuedJob, Worker  # moved to top-level
    # from .settings import get_setting  # moved to top-level

    # --- Rate limit: 1 req / 5s per session ---
    session_key = request.session.session_key
    if session_key:
        rl_key = f'sqlery_stats_rl_{session_key}'
        mem_cache = caches['default']
        if mem_cache.get(rl_key):
            # Return 429 so the JS can back off without logging an error
            return JsonResponse({'error': 'rate_limited'}, status=429)
        mem_cache.set(rl_key, 1, timeout=5)

    # --- 2-second in-memory cache (shared across sessions) ---
    cache_key = 'sqlery_dashboard_stats'
    mem_cache = caches['default']
    cached = mem_cache.get(cache_key)
    if cached is not None:
        return JsonResponse(cached)

    try:
        now = timezone.now()
        hour_ago = now - timedelta(hours=1)

        # Job counts by status
        # Queued count excludes future-scheduled jobs (matching worker claiming behavior)
        job_counts = QueuedJob.objects.aggregate(
            total=Count('id'),
            # queued=Count('id', filter=Q(status='queued')),
            queued=Count('id', filter=Q(status='queued') & (Q(scheduled_at__isnull=True) | Q(scheduled_at__lte=now))),
            scheduled=Count('id', filter=Q(status='queued', scheduled_at__gt=now)),
            running=Count('id', filter=Q(status='running')),
            success=Count('id', filter=Q(status='success')),
            failed=Count('id', filter=Q(status='failed')),
        )

        # Recent activity (last hour)
        recent_activity = QueuedJob.objects.filter(
            created_at__gte=hour_ago
        ).aggregate(
            recent_total=Count('id'),
            recent_success=Count('id', filter=Q(status='success')),
            recent_failed=Count('id', filter=Q(status='failed')),
        )

        # Queue statistics
        queue_stats = list(
            QueuedJob.objects.filter(status__in=['queued', 'running'])
            .values('queue_name')
            .annotate(
                # queued=Count('id', filter=Q(status='queued')),
                queued=Count('id', filter=Q(status='queued') & (Q(scheduled_at__isnull=True) | Q(scheduled_at__lte=now))),
                running=Count('id', filter=Q(status='running')),
                scheduled=Count('id', filter=Q(status='queued', scheduled_at__gt=now)),
            )
            .order_by('-queued', '-running')[:10]
        )

        # Scheduled tasks count per queue
        # scheduled_per_queue = dict(
        #     ScheduledTask.objects.filter(enabled=True)
        #     .values_list('queue_name')
        #     .annotate(count=Count('id'))
        #     .values_list('queue_name', 'count')
        # )

        # Scheduled tasks
        # scheduled_tasks = ScheduledTask.objects.aggregate(
        #     total=Count('id'),
        #     enabled_count=Count('id', filter=Q(enabled=True)),
        #     disabled_count=Count('id', filter=Q(enabled=False)),
        #     due_count=Count('id', filter=Q(enabled=True, next_run_at__lte=now)),
        # )

        # Scheduled tasks list (single query — per-queue and aggregate counts derived in Python)
        scheduled_tasks_list = list(
            ScheduledTask.objects.order_by('-enabled', 'name').values(
                'id', 'name', 'task_path', 'schedule_type', 'cron_expression',
                'interval', 'interval_unit', 'repeat', 'scheduled_time',
                'task_kwargs', 'queue_name', 'priority', 'enabled',
                'next_run_at', 'last_run_at'
            )
        )

        # Derive enabled_per_queue from already-fetched list
        enabled_per_queue = {}
        for _t in scheduled_tasks_list:
            if _t['enabled']:
                enabled_per_queue[_t['queue_name']] = enabled_per_queue.get(_t['queue_name'], 0) + 1

        # Derive aggregate counts from already-fetched list
        scheduled_tasks = {
            'total': len(scheduled_tasks_list),
            'enabled_count': sum(1 for _t in scheduled_tasks_list if _t['enabled']),
            'disabled_count': sum(1 for _t in scheduled_tasks_list if not _t['enabled']),
            'due_count': sum(
                1 for _t in scheduled_tasks_list
                if _t['enabled'] and _t['next_run_at'] and _t['next_run_at'] <= now
            ),
        }

        # Recent jobs (last 50)
        recent_jobs = list(
            QueuedJob.objects.order_by('-created_at')[:50].values(
                'id', 'task_path', 'status', 'queue_name',
                'priority', 'created_at', 'duration_seconds'
            )
        )

        # Format datetimes as ISO strings
        for job in recent_jobs:
            if job['created_at']:
                job['created_at'] = job['created_at'].isoformat()

        # ALL RUNNING JOBS - explicitly show what's currently executing
        all_running_jobs = []
        try:
            running_jobs_queryset = QueuedJob.objects.filter(
                status='running'
            ).select_related('scheduled_task')

            # Single query for all workers assigned to running jobs (avoid N+1)
            _running_jobs_list = list(running_jobs_queryset)
            running_job_ids = [j.id for j in _running_jobs_list]
            workers_by_job_id = {
                w.current_job_id: w
                for w in Worker.objects.filter(current_job_id__in=running_job_ids, status='busy')
            }

            for job in running_jobs_queryset:
                assigned_worker = None
                try:
                    worker = workers_by_job_id.get(job.id)
                    if worker:
                        assigned_worker = {
                            'id': str(worker.id),
                            'friendly_name': worker.friendly_name,
                            'node_id': worker.node_id,
                            'pid': worker.pid,
                            'heartbeat_age': (now - worker.last_heartbeat).total_seconds() if worker.last_heartbeat else None,
                        }
                except Exception:
                    pass

                job_data = {
                    'id': job.id,
                    'task_path': job.task_path,
                    'job_name': job.job_name,
                    'task_name': _job_display_name(job),
                    'status': job.status,
                    'queue_name': job.queue_name,
                    'started_at': job.started_at.isoformat() if job.started_at else None,
                    'timeout_seconds': job.timeout_seconds,
                    'worker': assigned_worker,
                }

                if job.started_at:
                    elapsed = (now - job.started_at).total_seconds()
                    job_data['elapsed_seconds'] = elapsed
                    job_data['is_timeout'] = bool(job.timeout_seconds and elapsed > job.timeout_seconds)
                else:
                    job_data['elapsed_seconds'] = None
                    job_data['is_timeout'] = False

                all_running_jobs.append(job_data)
        except Exception as e:
            logger.warning(f"Failed to fetch running jobs: {e}")

        # ALL QUEUED JOBS
        all_queued_jobs = []
        _oldest_queued_created_at = None
        try:
            queued_jobs_queryset = QueuedJob.objects.filter(
                status='queued'
            ).filter(
                # Match worker claiming: only show due jobs (not future-scheduled)
                Q(scheduled_at__isnull=True) | Q(scheduled_at__lte=now)
            ).select_related('scheduled_task').order_by('-priority', 'created_at')[:50]
            for job in queued_jobs_queryset:
                if _oldest_queued_created_at is None or job.created_at < _oldest_queued_created_at:
                    _oldest_queued_created_at = job.created_at
                all_queued_jobs.append({
                    'id': job.id,
                    'task_path': job.task_path,
                    'task_name': _job_display_name(job),
                    'queue_name': job.queue_name,
                    'priority': job.priority,
                    'created_at': job.created_at.isoformat(),
                    'scheduled_at': job.scheduled_at.isoformat() if job.scheduled_at else None,
                })
        except Exception as e:
            logger.warning(f"Failed to fetch queued jobs: {e}")

        # ALL SCHEDULED JOBS (future-scheduled, not yet due)
        all_scheduled_jobs = []
        try:
            scheduled_jobs_queryset = QueuedJob.objects.filter(
                status='queued',
                scheduled_at__gt=now,
            ).select_related('scheduled_task').order_by('scheduled_at')[:50]
            for job in scheduled_jobs_queryset:
                all_scheduled_jobs.append({
                    'id': job.id,
                    'task_path': job.task_path,
                    'task_name': _job_display_name(job),
                    'queue_name': job.queue_name,
                    'priority': job.priority,
                    'created_at': job.created_at.isoformat(),
                    'scheduled_at': job.scheduled_at.isoformat() if job.scheduled_at else None,
                })
        except Exception as e:
            logger.warning(f"Failed to fetch scheduled jobs: {e}")

        # ALL JOBS SUMMARY - complete picture of job states
        all_jobs_summary = list(
            QueuedJob.objects.values('status', 'queue_name')
            .annotate(count=Count('id'))
            .order_by('queue_name', 'status')
        )

        # job_runs removed — was fetching 500 rows on every poll (expensive).
        # Use the activity feed endpoint (/admin/api/sqlery/feed/) instead.
        job_runs = []

        for task in scheduled_tasks_list:
            # Null out next_run_at for disabled tasks or past-due values —
            # disabled tasks won't run and stale timestamps mislead the dashboard.
            if task['next_run_at'] and (not task['enabled'] or task['next_run_at'] <= now):
                task['next_run_at'] = None
            if task['next_run_at']:
                task['next_run_at'] = task['next_run_at'].isoformat()
            if task['last_run_at']:
                task['last_run_at'] = task['last_run_at'].isoformat()
            if task.get('scheduled_time'):
                task['scheduled_time'] = task['scheduled_time'].isoformat()

        # Worker statistics
        max_workers = get_setting('MAX_WORKERS_PER_NODE', 1)
        multi_worker_enabled = max_workers > 1

        # Get worker counts
        worker_counts = Worker.objects.exclude(pid=0).aggregate(
            active=Count('id', filter=Q(status__in=['idle', 'busy'])),
            idle=Count('id', filter=Q(status='idle')),
            busy=Count('id', filter=Q(status='busy')),
            dead=Count('id', filter=Q(status='dead')),
        )

        worker_stats = {
            'max_workers': max_workers,
            'active': worker_counts['active'],
            'idle': worker_counts['idle'],
            'busy': worker_counts['busy'],
            'dead': worker_counts['dead'],
        }

        # Get active workers list with current job info
        # REGRESSION 2026-06-16: stale workers (heartbeat 100s of hours old) never disappeared from the dashboard.
        # Root cause: the query filtered on status in ('idle','busy') but had no upper bound on heartbeat age,
        #   so a worker that died without updating its status row stayed 'idle' and rendered forever.
        # Fix: exclude workers whose last_heartbeat is older than 24h from the dashboard listing.
        workers_list = []
        try:
            stale_cutoff = now - timedelta(hours=24)
            # I wish I had the time to: make the 24h dashboard cutoff configurable via DJANGO_SQL_JOBS settings.
            # Old: .select_related('current_job', 'current_job__scheduled_task')  — FK demoted (D4, Phase 15)
            workers_queryset = (
                Worker.objects.filter(status__in=['idle', 'busy'])
                .exclude(pid=0)
                .exclude(last_heartbeat__lt=stale_cutoff)
                .order_by('-last_heartbeat')
            )
            _active_workers_list = list(workers_queryset)

            for worker in _active_workers_list:
                workers_list.append(_serialize_worker(worker, now))
        except Exception as e:
            logger.warning(f"Failed to fetch workers list: {e}")

        # Irregularity detection — only run once per minute, not every poll
        # The daemon already handles this; the stats endpoint should be read-only
        irregularities = {
            'stalled_workers_fixed': 0,
            'timed_out_jobs_fixed': 0,
            'dead_workers_cleaned': 0,
            'stuck_jobs_reset': 0,
            'details': [],
        }

        # All configured queues with stats (include zero-count queues)
        configured_queues = get_setting('WORKER_QUEUES', ['high', 'default', 'low'])
        queue_stats_map = {q['queue_name']: q for q in queue_stats}

        all_queues = []
        for queue_name in configured_queues:
            if queue_name in queue_stats_map:
                entry = queue_stats_map[queue_name].copy()
                entry['enabled'] = enabled_per_queue.get(queue_name, 0)
                all_queues.append(entry)
            else:
                all_queues.append({
                    'queue_name': queue_name,
                    'queued': 0,
                    'running': 0,
                    'scheduled': 0,
                    'enabled': enabled_per_queue.get(queue_name, 0),
                })

        for queue_name, stats in queue_stats_map.items():
            if queue_name not in configured_queues:
                entry = stats.copy()
                entry['enabled'] = enabled_per_queue.get(queue_name, 0)
                all_queues.append(entry)

        # SYSTEM HEALTH - flags and warnings
        system_health = {
            'has_running_jobs': len(all_running_jobs) > 0,
            'has_queued_jobs': job_counts['queued'] > 0,
            'has_active_workers': worker_stats['active'] > 0,
            'has_stalled_workers': any(w.get('is_stalled', False) for w in workers_list),
            'has_timeout_jobs': any(j.get('is_timeout', False) for j in all_running_jobs),
            'has_irregularities': False,
            'warning': None,
            'is_stuck': False,
        }

        if system_health['has_queued_jobs'] and not system_health['has_running_jobs'] and system_health['has_active_workers']:
            # oldest_queued = QueuedJob.objects.filter(
            #     status='queued'
            # ).filter(
            #     Q(scheduled_at__isnull=True) | Q(scheduled_at__lte=now)
            # ).order_by('created_at').first()
            # Derive from in-memory data (tracked during queued jobs loop above)
            if _oldest_queued_created_at:
                oldest_age = (now - _oldest_queued_created_at).total_seconds()
                system_health['warning'] = f'Workers not picking up jobs (oldest queued: {oldest_age:.1f}s)'
                system_health['is_stuck'] = oldest_age > 11

        payload = {
            'timestamp': now.isoformat(),
            'job_counts': job_counts,
            'recent_activity': recent_activity,
            'queue_stats': all_queues,
            'scheduled_tasks': scheduled_tasks,
            'scheduled_tasks_list': scheduled_tasks_list,
            'recent_jobs': recent_jobs,
            'job_runs': job_runs,
            'all_running_jobs': all_running_jobs,
            'all_queued_jobs': all_queued_jobs,
            'all_scheduled_jobs': all_scheduled_jobs,
            'all_jobs_summary': all_jobs_summary,
            'multi_worker_enabled': multi_worker_enabled,
            'worker_stats': worker_stats,
            'workers_list': workers_list,
            'irregularities': irregularities,
            'system_health': system_health,
            'health_warnings': _get_health_warnings(
                now,
                precomputed_workers=_active_workers_list,
                precomputed_queued_count=job_counts['queued'],
                precomputed_running_count=job_counts['running'],
                precomputed_running_jobs=_running_jobs_list,
            ),
        }
        mem_cache.set(cache_key, payload, timeout=2)
        return JsonResponse(payload)

    except Exception as e:
        logger.error(f"Dashboard stats failed: {e}", exc_info=True)
        return JsonResponse(
            {'error': str(e)}, status=500
        )


@staff_member_required
def dump_scheduled_tasks(request):
    """Dump ScheduledTask records as a Django fixture with natural keys."""
    buf = StringIO()
    call_command(
        "dumpdata",
        "sqlery.scheduledtask",
        stdout=buf,
        indent=2,
        use_natural_foreign_keys=True,
        use_natural_primary_keys=True,
    )
    timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
    response = HttpResponse(buf.getvalue(), content_type="application/json")
    response["Content-Disposition"] = f'attachment; filename="sqlery-tasks_{timestamp}.json"'
    return response


@staff_member_required
def load_scheduled_tasks(request):
    """Load ScheduledTask records from an uploaded Django fixture."""
    if request.method == "POST":
        uploaded = request.FILES.get("fixture_file")
        if not uploaded:
            messages.error(request, "No file uploaded.")
            return redirect("sqlery:load_tasks")

        tmp = None
        try:
            tmp = tempfile.NamedTemporaryFile(
                suffix=".json", delete=False, mode="wb",
            )
            for chunk in uploaded.chunks():
                tmp.write(chunk)
            tmp.close()

            buf = StringIO()
            call_command("loaddata", tmp.name, stdout=buf, verbosity=1)
            messages.success(request, f"Loaded successfully: {buf.getvalue().strip()}")
        except Exception as exc:
            messages.error(request, f"Load failed: {exc}")
        finally:
            if tmp:
                # import os as _os  # moved to top-level
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass

        return redirect("sqlery:unified_view")

    return render(request, "admin/sqlery/load_tasks.html")



# ============================================================================
# Pure-core HTTP trigger adapter (SMOD-03 / CONTEXT D)
# ============================================================================
#
# This view is the Django-side adapter that delegates to
# sqlery.core.triggers.handle. The legacy `internal_worker` view above is
# preserved unchanged for back-compat (it spawns a subprocess); the new
# trigger_view is the path that matches the FastAPI router and the spec'd
# envelope/result shape.

@csrf_exempt
@require_POST
def trigger_view(request):
    """Receive an HTTP trigger envelope and call core.triggers.handle.

    Django side of the pure-core HTTP trigger (SMOD-03). Translates the
    Django request into a TriggerEnvelope, calls handle(), translates the
    TriggerResult to a JsonResponse.
    """
    import json as _json
    from sqlery.core.triggers import TriggerEnvelope, handle as _handle

    body = request.body or b""
    headers = {k: v for k, v in request.headers.items()}
    payload = {}
    if body:
        try:
            payload = _json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            logger.warning(f"Invalid trigger payload: {e}")
            return JsonResponse({"error": "invalid JSON payload"}, status=400)

    # Use the real socket peer (REMOTE_ADDR), never X-Forwarded-For, which is
    # attacker-controllable and must not gate the IP allowlist.
    remote_addr = request.META.get("REMOTE_ADDR")
    envelope = TriggerEnvelope(
        body=body, headers=headers, payload=payload, remote_addr=remote_addr
    )
    result = _handle(envelope)
    return JsonResponse(result.body, status=result.status_code)
