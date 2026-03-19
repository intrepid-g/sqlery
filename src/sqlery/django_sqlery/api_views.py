"""API views for SQLery admin - all endpoints return JSON for API-first architecture."""

import json
import logging
import os
import signal
import time
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path

from django.conf import settings as django_settings
from django.core.paginator import Paginator
from django.db.models import Avg, Count, Max, Min, Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from ..compat import get_backend
from .intervention import diagnose_system_health, do_manual_intervention_direct
from .models import DaemonCommand, QueuedJob, ScheduledTask, Worker
from .utils import enqueue_task
from .views import staff_required_json, _compute_health_warnings, _get_health_warnings, _job_display_name

logger = logging.getLogger(__name__)


@staff_required_json
def api_scheduled_tasks_list(request):
    """GET /admin/api/sqlery/tasks/

    List all scheduled tasks with job counts.

    Returns:
        JSON with tasks array containing task details and statistics
    """
    try:
        tasks = ScheduledTask.objects.annotate(
            total_jobs=Count('jobs'),
            failed_jobs=Count('jobs', filter=Q(jobs__status='failed')),
            queued_jobs=Count('jobs', filter=Q(jobs__status='queued'))
        ).order_by('-enabled', 'name')

        # from django.utils import timezone as _tz  # moved to top-level
        _now = timezone.now()

        data = []
        for t in tasks:
            # Null out next_run_at for disabled tasks or past-due values
            next_run = t.next_run_at
            if next_run and (not t.enabled or next_run <= _now):
                next_run = None
            data.append({
                'id': t.id,
                'name': t.name,
                'task_path': t.task_path,
                'schedule_type': t.schedule_type,
                'cron_expression': t.cron_expression,
                'interval': t.interval,
                'interval_unit': t.interval_unit,
                'repeat': t.repeat,
                'scheduled_time': t.scheduled_time.isoformat() if t.scheduled_time else None,
                'schedule_display': t.schedule_display(),
                'task_kwargs': t.task_kwargs,
                'enabled': t.enabled,
                'queue_name': t.queue_name,
                'priority': t.priority,
                'last_run_at': t.last_run_at.isoformat() if t.last_run_at else None,
                'next_run_at': next_run.isoformat() if next_run else None,
                # Numeric sort key (epoch seconds) — avoids JS date parsing issues
                'next_run_sort': next_run.timestamp() if next_run else None,
                'total_jobs': t.total_jobs,
                'failed_jobs': t.failed_jobs,
                'queued_jobs': t.queued_jobs,
            })

        return JsonResponse({'tasks': data})

    except Exception as e:
        logger.error(f"Failed to fetch tasks list: {e}")
        return JsonResponse({'error': str(e)}, status=500)


@staff_required_json
def api_task_detail(request, task_id):
    """GET /admin/api/sqlery/tasks/<task_id>/

    Get detailed information about a specific task.

    Args:
        task_id: Primary key of the ScheduledTask

    Returns:
        JSON with task details, statistics, and latest job info
    """
    try:
        task = ScheduledTask.objects.get(id=task_id)
    except ScheduledTask.DoesNotExist:
        return JsonResponse({'error': 'Task not found'}, status=404)

    try:
        # Get latest job
        latest_job = task.jobs.order_by('-created_at').first()

        # Get job statistics
        jobs = task.jobs.all()
        stats = {
            'total': jobs.count(),
            'queued': jobs.filter(status='queued').count(),
            'running': jobs.filter(status='running').count(),
            'success': jobs.filter(status='success').count(),
            'failed': jobs.filter(status='failed').count(),
        }

        data = {
            'id': task.id,
            'name': task.name,
            'task_path': task.task_path,
            'schedule_type': task.schedule_type,
            'cron_expression': task.cron_expression,
            'interval': task.interval,
            'interval_unit': task.interval_unit,
            'repeat': task.repeat,
            'scheduled_time': task.scheduled_time.isoformat() if task.scheduled_time else None,
            'schedule_display': task.schedule_display(),
            'task_kwargs': task.task_kwargs,
            'enabled': task.enabled,
            'queue_name': task.queue_name,
            'priority': task.priority,
            'last_run_at': task.last_run_at.isoformat() if task.last_run_at else None,
            'next_run_at': task.next_run_at.isoformat() if task.next_run_at else None,
            'created_at': task.created_at.isoformat(),
            'updated_at': task.updated_at.isoformat(),
            'stats': stats,
            'latest_job': {
                'id': latest_job.id,
                'status': latest_job.status,
                'created_at': latest_job.created_at.isoformat(),
                'started_at': latest_job.started_at.isoformat() if latest_job.started_at else None,
                'finished_at': latest_job.finished_at.isoformat() if latest_job.finished_at else None,
                'duration_seconds': latest_job.duration_seconds,
                'error': latest_job.error,
                'output': latest_job.output,
            } if latest_job else None,
        }

        return JsonResponse(data)

    except Exception as e:
        logger.error(f"Failed to fetch task detail for task_id={task_id}: {e}")
        return JsonResponse({'error': str(e)}, status=500)


@staff_required_json
def api_task_jobs(request, task_id):
    """GET /admin/api/sqlery/tasks/<task_id>/jobs/?page=1&limit=20&status=failed

    Get paginated job history for a specific task.

    Args:
        task_id: Primary key of the ScheduledTask

    Query Parameters:
        page (int): Page number (default: 1)
        limit (int): Jobs per page (default: 20, max: 100)
        status (str): Filter by status - queued, running, success, failed

    Returns:
        JSON with paginated job list
    """
    try:
        task = ScheduledTask.objects.get(id=task_id)
    except ScheduledTask.DoesNotExist:
        return JsonResponse({'error': 'Task not found'}, status=404)

    try:
        # Parse query parameters
        page = int(request.GET.get('page', 1))
        limit = min(int(request.GET.get('limit', 20)), 100)  # Cap at 100
        status_filter = request.GET.get('status')  # queued, running, success, failed

        # Query jobs
        jobs = task.jobs.order_by('-created_at')
        if status_filter:
            jobs = jobs.filter(status=status_filter)

        # Paginate
        paginator = Paginator(jobs, limit)
        page_obj = paginator.get_page(page)

        data = {
            'page': page,
            'total_pages': paginator.num_pages,
            'total_count': paginator.count,
            'jobs': [{
                'id': j.id,
                'status': j.status,
                'created_at': j.created_at.isoformat(),
                'scheduled_at': j.scheduled_at.isoformat() if j.scheduled_at else None,
                'started_at': j.started_at.isoformat() if j.started_at else None,
                'finished_at': j.finished_at.isoformat() if j.finished_at else None,
                'duration_seconds': j.duration_seconds,
                'retry_count': j.retry_count,
                'output': j.output,
                'error': j.error,
                'traceback': j.traceback,
                'worker_pid': j.worker_pid,
            } for j in page_obj]
        }

        return JsonResponse(data)

    except ValueError as e:
        return JsonResponse({'error': f'Invalid query parameter: {e}'}, status=400)
    except Exception as e:
        logger.error(f"Failed to fetch jobs for task_id={task_id}: {e}")
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@staff_required_json
def api_task_action(request, task_id):
    """POST /admin/api/sqlery/tasks/<task_id>/action/

    Perform an action on a task.

    Args:
        task_id: Primary key of the ScheduledTask

    Request Body (JSON):
        {
            "action": "enqueue" | "enable" | "disable"
        }

    Returns:
        JSON with success status and job_id (for enqueue action)
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        task = ScheduledTask.objects.get(id=task_id)
    except ScheduledTask.DoesNotExist:
        return JsonResponse({'error': 'Task not found'}, status=404)

    try:
        # Parse request body
        body = json.loads(request.body)
        action = body.get('action')

        if action == 'enqueue':
            # Enqueue task immediately - allow multiple jobs
            # from .utils import enqueue_task  # moved to top-level
            job = enqueue_task(task)
            return JsonResponse({'success': True, 'job_id': job.id})

        elif action == 'enable':
            # Enable task
            task.enabled = True
            task.save(update_fields=['enabled', 'updated_at'])
            return JsonResponse({'success': True})

        elif action == 'disable':
            # Disable task
            task.enabled = False
            task.save(update_fields=['enabled', 'updated_at'])
            return JsonResponse({'success': True})

        elif action == 'delete':
            # Delete task - only if disabled
            if task.enabled:
                return JsonResponse({
                    'error': 'Cannot delete an enabled task. Disable it first.',
                }, status=400)
            task_name = task.name
            task.delete()
            return JsonResponse({'success': True, 'deleted': task_name})

        else:
            return JsonResponse({
                'error': f'Invalid action: {action}',
                'valid_actions': ['enqueue', 'enable', 'disable', 'delete']
            }, status=400)

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON in request body'}, status=400)
    except Exception as e:
        logger.error(f"Failed to perform action on task_id={task_id}: {e}")
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@staff_required_json
def api_stop_job(request, job_id):
    """POST /admin/api/sqlery/jobs/<job_id>/stop/

    Stop a running job by killing its worker process.

    Returns:
        JSON with success status
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        job = QueuedJob.objects.get(id=job_id)
    except QueuedJob.DoesNotExist:
        return JsonResponse({'error': 'Job not found'}, status=404)

    if job.status != 'running':
        return JsonResponse({'error': f'Job is not running (status: {job.status})'}, status=400)

    try:
        # import os  # moved to top-level
        # import signal  # moved to top-level

        # # Old: found worker and killed worker_pid (which is the parent).
        # # This killed the parent worker, not the forked child.
        # from .models import Worker
        # worker = Worker.objects.filter(current_job=job, status='busy').first()
        #
        # killed = False
        # if job.worker_pid:
        #     try:
        #         os.kill(job.worker_pid, signal.SIGTERM)
        #         killed = True
        #     except OSError:
        #         pass
        # elif worker and worker.pid:
        #     try:
        #         os.kill(worker.pid, signal.SIGTERM)
        #         killed = True
        #     except OSError:
        #         pass
        #
        # job.mark_failed(
        #     error="Stopped by admin user",
        #     termination_reason="stopped_by_user",
        # )
        #
        # if worker:
        #     worker.status = 'idle'
        #     worker.current_job = None
        #     worker.save(update_fields=['status', 'current_job', 'last_heartbeat'])

        killed = False
        # Kill the forked child (not the parent worker)
        if job.child_pid:
            try:
                os.killpg(os.getpgid(job.child_pid), signal.SIGTERM)
                killed = True
            except (OSError, ProcessLookupError):
                try:
                    os.kill(job.child_pid, signal.SIGTERM)
                    killed = True
                except OSError:
                    pass
        elif job.worker_pid:
            # Legacy fallback for jobs without child_pid
            try:
                os.kill(job.worker_pid, signal.SIGTERM)
                killed = True
            except OSError:
                pass

        # Mark job as failed
        job.mark_failed(
            error="Stopped by admin user",
            termination_reason="stopped_by_user",
        )

        # Don't touch worker status — the parent is still alive and will
        # update its own state after detecting the child's exit via waitpid.

        return JsonResponse({
            'success': True,
            'killed': killed,
            'job_id': job.id,
        })

    except Exception as e:
        logger.error(f"Failed to stop job {job_id}: {e}")
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@staff_required_json
def api_worker_action(request, worker_id):
    """POST /admin/api/sqlery/workers/<worker_id>/action/

    Actions: pause, unpause, pause_for (with seconds param).
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    # from .models import Worker  # moved to top-level
    # import uuid as uuid_mod  # moved to top-level

    try:
        worker = Worker.objects.get(id=uuid.UUID(worker_id))
    except (ValueError, Worker.DoesNotExist):
        return JsonResponse({'error': 'Worker not found'}, status=404)

    try:
        body = json.loads(request.body)
        action = body.get('action')

        if action == 'pause':
            # Pause indefinitely (far future)
            worker.paused_until = timezone.now() + timedelta(days=365)
            worker.save(update_fields=['paused_until'])
            return JsonResponse({'success': True, 'paused_until': worker.paused_until.isoformat()})

        elif action == 'unpause':
            worker.paused_until = None
            worker.save(update_fields=['paused_until'])
            return JsonResponse({'success': True})

        elif action == 'pause_for':
            seconds = int(body.get('seconds', 300))
            worker.paused_until = timezone.now() + timedelta(seconds=seconds)
            worker.save(update_fields=['paused_until'])
            return JsonResponse({'success': True, 'paused_until': worker.paused_until.isoformat()})

        elif action == 'restart':
            # Send SIGTERM to the worker process — the daemon will detect the dead PID
            # within DAEMON_CHECK_INTERVAL seconds and spawn a replacement automatically.
            # import os  # moved to top-level
            # import signal as _signal  # moved to top-level

            if not worker.pid:
                return JsonResponse({'error': 'Worker has no PID'}, status=400)

            sent = False
            try:
                os.kill(worker.pid, signal.SIGTERM)
                sent = True
            except ProcessLookupError:
                pass  # already gone — daemon will clean it up and spawn a new one
            except PermissionError:
                return JsonResponse({'error': f'No permission to signal PID {worker.pid}'}, status=403)

            # Mark dead immediately so the daemon replaces it on its next check
            worker.status = 'dead'
            worker.save(update_fields=['status'])

            return JsonResponse({
                'success': True,
                'signal_sent': sent,
                'pid': worker.pid,
                'msg': 'Worker will be replaced by the daemon within ~10 seconds.',
            })

        else:
            return JsonResponse({'error': f'Invalid action: {action}'}, status=400)

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        logger.error(f"Worker action failed for {worker_id}: {e}")
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@staff_required_json
def api_remove_queued_job(request, job_id):
    """POST /admin/api/sqlery/jobs/<job_id>/remove/

    Remove a queued job (delete it). Only works for queued status.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        job = QueuedJob.objects.get(id=job_id)
    except QueuedJob.DoesNotExist:
        return JsonResponse({'error': 'Job not found'}, status=404)

    if job.status != 'queued':
        return JsonResponse({'error': f'Job is not queued (status: {job.status})'}, status=400)

    job.delete()
    return JsonResponse({'success': True, 'job_id': job_id})


@csrf_exempt
@staff_required_json
def api_enqueue_job_now(request, job_id):
    """POST /admin/api/sqlery/jobs/<job_id>/enqueue-now/

    Clear scheduled_at on a queued job so it becomes immediately eligible for pickup.
    Only works for queued jobs with a future scheduled_at.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        job = QueuedJob.objects.get(id=job_id)
    except QueuedJob.DoesNotExist:
        return JsonResponse({'error': 'Job not found'}, status=404)

    if job.status != 'queued':
        return JsonResponse({'error': f'Job is not queued (status: {job.status})'}, status=400)

    if not job.scheduled_at or job.scheduled_at <= timezone.now():
        return JsonResponse({'error': 'Job is not scheduled for the future'}, status=400)

    job.scheduled_at = None
    job.save(update_fields=['scheduled_at'])
    return JsonResponse({'success': True, 'job_id': job.id})


@staff_required_json
def api_worker_detail(request, worker_id):
    """GET /admin/api/sqlery/workers/<worker_id>/

    Get detailed worker info including current job and recent job history.
    """
    # from .models import Worker  # moved to top-level
    # import uuid as uuid_mod  # moved to top-level

    try:
        worker_uuid = uuid.UUID(worker_id)
        worker = Worker.objects.select_related('current_job', 'current_job__scheduled_task').get(id=worker_uuid)
    except (ValueError, Worker.DoesNotExist):
        return JsonResponse({'error': 'Worker not found'}, status=404)

    now = timezone.now()

    # Current job
    current_job = None
    if worker.current_job:
        j = worker.current_job
        elapsed = (now - j.started_at).total_seconds() if j.started_at else None
        current_job = {
            'id': j.id,
            'task_path': j.task_path,
            'task_name': _job_display_name(j),
            'status': j.status,
            'queue_name': j.queue_name,
            'started_at': j.started_at.isoformat() if j.started_at else None,
            'elapsed_seconds': elapsed,
        }

    # Job history — query by worker FK (persisted after completion)
    recent_jobs = QueuedJob.objects.filter(
        worker=worker
    ).exclude(status__in=['queued', 'running']).select_related('scheduled_task').order_by('-finished_at')[:50]

    jobs_history = [{
        'id': j.id,
        'task_path': j.task_path,
        'task_name': _job_display_name(j),
        'status': j.status,
        'queue_name': j.queue_name,
        'created_at': j.created_at.isoformat(),
        'started_at': j.started_at.isoformat() if j.started_at else None,
        'finished_at': j.finished_at.isoformat() if j.finished_at else None,
        'duration_seconds': j.duration_seconds,
        'error': j.error,
    } for j in recent_jobs]

    # Upcoming (queued) jobs for this worker's queues
    worker_queues = worker.queues or []
    # from django.utils import timezone as _tz  # moved to top-level
    _now = timezone.now()
    if worker_queues:
        upcoming_qs = QueuedJob.objects.filter(
            status='queued', queue_name__in=worker_queues
        ).filter(Q(scheduled_at__isnull=True) | Q(scheduled_at__lte=_now))
    else:
        upcoming_qs = QueuedJob.objects.filter(
            status='queued'
        ).filter(Q(scheduled_at__isnull=True) | Q(scheduled_at__lte=_now))
    upcoming_jobs = [{
        'id': j.id,
        'task_path': j.task_path,
        'task_name': _job_display_name(j),
        'queue_name': j.queue_name,
        'priority': j.priority,
        'created_at': j.created_at.isoformat(),
        'scheduled_at': j.scheduled_at.isoformat() if j.scheduled_at else None,
    } for j in upcoming_qs.select_related('scheduled_task').order_by('-priority', 'created_at')[:20]]

    # Stats — use worker FK to find all jobs this worker has processed
    # from django.db.models import Avg  # moved to top-level
    worker_jobs_qs = QueuedJob.objects.filter(worker=worker)
    job_stats = worker_jobs_qs.aggregate(
        total=Count('id'),
        success=Count('id', filter=Q(status='success')),
        failed=Count('id', filter=Q(status='failed')),
        avg_duration=Avg('duration_seconds', filter=Q(duration_seconds__isnull=False)),
    )

    heartbeat_age = (now - worker.last_heartbeat).total_seconds() if worker.last_heartbeat else None

    data = {
        'id': str(worker.id),
        'node_id': worker.node_id,
        'pid': worker.pid,
        'status': worker.status,
        'queues': worker.queues,
        'started_at': worker.started_at.isoformat() if worker.started_at else None,
        'last_heartbeat': worker.last_heartbeat.isoformat() if worker.last_heartbeat else None,
        'heartbeat_age': heartbeat_age,
        'paused_until': worker.paused_until.isoformat() if worker.paused_until else None,
        'is_paused': bool(worker.paused_until and worker.paused_until > now),
        'jobs_processed': worker.jobs_processed,
        'current_job': current_job,
        'upcoming_jobs': upcoming_jobs,
        'job_stats': job_stats,
        'jobs_history': jobs_history,
    }

    return JsonResponse(data)


@csrf_exempt
@staff_required_json
def api_job_priority(request, job_id):
    """POST /admin/api/sqlery/jobs/<job_id>/priority/

    Change priority of a queued job.

    Request Body (JSON):
        {"action": "bump_up" | "bump_down" | "move_top" | "move_bottom"}
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        job = QueuedJob.objects.get(id=job_id)
    except QueuedJob.DoesNotExist:
        return JsonResponse({'error': 'Job not found'}, status=404)

    if job.status != 'queued':
        return JsonResponse({'error': f'Job is not queued (status: {job.status})'}, status=400)

    try:
        body = json.loads(request.body)
        action = body.get('action')

        if action == 'bump_up':
            job.priority += 10
        elif action == 'bump_down':
            job.priority -= 10
        elif action == 'move_top':
            max_pri = QueuedJob.objects.filter(
                status='queued', queue_name=job.queue_name,
            ).aggregate(m=Max('priority'))['m'] or 0
            job.priority = max_pri + 10
        elif action == 'move_bottom':
            min_pri = QueuedJob.objects.filter(
                status='queued', queue_name=job.queue_name,
            ).aggregate(m=Min('priority'))['m'] or 0
            job.priority = min_pri - 10
        else:
            return JsonResponse({
                'error': f'Invalid action: {action}',
                'valid_actions': ['bump_up', 'bump_down', 'move_top', 'move_bottom'],
            }, status=400)

        job.save(update_fields=['priority'])
        return JsonResponse({'success': True, 'job_id': job.id, 'priority': job.priority, 'action': action})

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON in request body'}, status=400)
    except Exception as e:
        logger.error(f"Failed to change priority for job {job_id}: {e}")
        return JsonResponse({'error': str(e)}, status=500)


@staff_required_json
def api_queue_jobs(request, queue_name):
    """GET /admin/api/sqlery/queues/<queue_name>/jobs/

    Get jobs for a specific queue: queued, running, and recently finished (last 60s).
    """
    try:
        now = timezone.now()
        cutoff = now - timedelta(seconds=60)

        jobs = QueuedJob.objects.filter(queue_name=queue_name).filter(
            Q(status__in=['queued', 'running']) |
            Q(status__in=['success', 'failed'], finished_at__gte=cutoff)
        ).select_related('scheduled_task').order_by(
            # running first, then queued, then finished
            '-status',  # running > queued > success > failed (alphabetically desc)
            '-created_at',
        )[:50]

        data = [{
            'id': j.id,
            'status': j.status,
            'task_path': j.task_path,
            'task_name': _job_display_name(j),
            'created_at': j.created_at.isoformat(),
            'started_at': j.started_at.isoformat() if j.started_at else None,
            'finished_at': j.finished_at.isoformat() if j.finished_at else None,
            'duration_seconds': j.duration_seconds,
            'error': j.error,
            'worker_pid': j.worker_pid,
        } for j in jobs]

        return JsonResponse({'jobs': data, 'queue_name': queue_name})

    except Exception as e:
        logger.error(f"Failed to fetch queue jobs for {queue_name}: {e}")
        return JsonResponse({'error': str(e)}, status=500)


@staff_required_json
def api_activity_feed(request):
    """GET /admin/api/sqlery/feed/?since=<iso>&limit=100

    Returns recent job events (queued, started, finished, failed) derived
    from job timestamps.  Events are sorted newest-first.

    Query Parameters:
        since (str): ISO datetime — only return events after this time.
                     Defaults to 5 minutes ago.
        limit (int): Max events to return (default 100, max 500).
    """
    try:
        now = timezone.now()
        since_str = request.GET.get('since')
        if since_str:
            # from datetime import datetime as dt  # moved to top-level
            since = datetime.fromisoformat(since_str)
            if since.tzinfo is None:
                # from datetime import timezone as _tz  # moved to top-level
                since = since.replace(tzinfo=dt_timezone.utc)
        else:
            since = now - timedelta(minutes=5)

        limit = min(int(request.GET.get('limit', 100)), 500)

        # Fetch jobs that had any activity since the cutoff
        jobs = QueuedJob.objects.filter(
            Q(created_at__gte=since) |
            Q(started_at__gte=since) |
            Q(finished_at__gte=since)
        ).select_related('scheduled_task').order_by('-created_at')[:limit]

        events = []
        for j in jobs:
            name = _job_display_name(j)
            queue = j.queue_name

            # Queued event
            if j.created_at and j.created_at >= since:
                events.append({
                    'job_id': j.id,
                    'type': 'queued',
                    'msg': f'{name} queued on {queue}',
                    'name': name,
                    'queue': queue,
                    'time': j.created_at.isoformat(),
                })

            # Started event
            if j.started_at and j.started_at >= since:
                events.append({
                    'job_id': j.id,
                    'type': 'started',
                    'msg': f'{name} started on {queue}',
                    'name': name,
                    'queue': queue,
                    'time': j.started_at.isoformat(),
                })

            # Finished event
            if j.finished_at and j.finished_at >= since and j.status == 'success':
                dur = f' ({j.duration_seconds:.1f}s)' if j.duration_seconds else ''
                events.append({
                    'job_id': j.id,
                    'type': 'success',
                    'msg': f'{name} finished successfully{dur}',
                    'name': name,
                    'queue': queue,
                    'time': j.finished_at.isoformat(),
                })

            # Failed event
            if j.finished_at and j.finished_at >= since and j.status == 'failed':
                err = ''
                if j.error:
                    err = f' — {j.error[:80]}'
                # Include task_path as subtitle so the feed always shows what failed
                task_path_hint = j.task_path if j.task_path and j.task_path != name else None
                events.append({
                    'job_id': j.id,
                    'type': 'failed',
                    'msg': f'{name} failed{err}',
                    'name': name,
                    'subtitle': task_path_hint,
                    'queue': queue,
                    'error': j.error,
                    'time': j.finished_at.isoformat(),
                })

        # --- Health warnings (synthetic events, always at "now") ---
        # health_events = _compute_health_warnings(now)
        health_events = _get_health_warnings(now)
        events.extend(health_events)

        # Sort newest first
        events.sort(key=lambda e: e['time'], reverse=True)
        events = events[:limit]

        return JsonResponse({'events': events, 'since': since.isoformat()})

    except Exception as e:
        logger.error(f"Activity feed failed: {e}")
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@staff_required_json
def api_clear_jobs(request):
    """POST /admin/api/sqlery/jobs/clear/

    Clear completed jobs by status.

    Request Body (JSON):
        {"status": "failed"}  or  {"status": "success"}

    Returns:
        JSON with count of deleted jobs
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        body = json.loads(request.body)
        status = body.get('status')

        if status not in ('failed', 'success', 'archived'):
            return JsonResponse({'error': 'status must be "failed", "success", or "archived"'}, status=400)

        deleted_count, _ = QueuedJob.objects.filter(status=status).delete()
        return JsonResponse({'success': True, 'deleted': deleted_count, 'status': status})

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        logger.error(f"Clear jobs failed: {e}")
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@staff_required_json
def api_archive_scheduled_jobs(request):
    """POST /admin/api/sqlery/jobs/archive-scheduled/

    Archive one or more scheduled (future-dated) queued jobs by setting
    their status to 'archived'.

    Request Body (JSON):
        {"job_ids": [1, 2, 3]}

    Returns:
        JSON with count of archived jobs
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        body = json.loads(request.body)
        job_ids = body.get('job_ids', [])

        if not job_ids:
            return JsonResponse({'error': 'job_ids is required and must be non-empty'}, status=400)

        updated = QueuedJob.objects.filter(
            id__in=job_ids,
            status='queued',
        ).update(status='archived')

        return JsonResponse({'success': True, 'archived': updated})

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        logger.error(f"Archive scheduled jobs failed: {e}")
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@staff_required_json
def api_vacuum(request):
    """POST /admin/api/sqlery/vacuum/

    Run database vacuum and cleanup. Useful after bulk deletes.

    Returns:
        JSON with vacuum results
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        # from ..compat import get_backend  # moved to top-level
        backend = get_backend()
        result = backend.vacuum_database()
        return JsonResponse({'success': True, 'result': result})
    except Exception as e:
        logger.error(f"Vacuum failed: {e}")
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@staff_required_json
def api_manual_intervention(request):
    """POST /admin/api/sqlery/intervene/

    Trigger manual intervention via the daemon command queue.

    Creates a DaemonCommand(command='manual_intervention') and waits
    up to 15 seconds for the daemon to process it. If the daemon is
    alive, it will pick up the command on its next cycle (<=10s).

    If the daemon appears down, falls back to direct DB-only intervention.

    Returns:
        200: {"status": "completed", "result": {...}}
        202: {"status": "pending", "message": "..."}
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    # ── Gate: refuse to intervene if system is healthy ─────────
    #
    # diagnose_system_health() is a read-only check that returns a
    # list of detected problems. If empty, the system is working
    # correctly and intervention would only cause harm (killing
    # healthy workers, failing running jobs, etc.).
    #
    # check_os=False because this runs in the web server process,
    # which may be in a different container/PID namespace than the
    # workers. PID checks would be meaningless. DB-only checks
    # (stale heartbeats, ghost jobs, queue counts) are sufficient
    # to gate the request — the daemon will do the OS-level checks
    # when it processes the command.
    # from .intervention import diagnose_system_health  # moved to top-level
    problems = diagnose_system_health(check_os=False)
    if not problems:
        return JsonResponse({
            'status': 'rejected',
            'message': 'System appears healthy — no intervention needed',
            'checks_passed': [
                'All busy worker heartbeats are fresh (<120s)',
                'Daemon lease is not expired',
                'No ghost running jobs',
                'No queued jobs without workers',
                'No running jobs past their timeout',
            ],
        }, status=409)

    # Check if there's already a pending intervention
    existing = DaemonCommand.objects.filter(
        command='manual_intervention',
        status='pending',
    ).first()
    if existing:
        return JsonResponse({
            'status': 'pending',
            'message': 'Intervention already queued',
            'command_id': existing.id,
        }, status=202)

    # Create the command (include diagnosis so daemon doesn't re-check)
    cmd = DaemonCommand.objects.create(
        command='manual_intervention',
        payload={
            'triggered_by': 'dashboard',
            'triggered_at': timezone.now().isoformat(),
        },
    )

    # Wait for daemon to pick it up (up to 15 seconds, polling every 1s)
    for _ in range(15):
        time.sleep(1)
        cmd.refresh_from_db()
        if cmd.status in ('completed', 'failed'):
            return JsonResponse({
                'status': cmd.status,
                'result': cmd.result,
                'processed_at': cmd.processed_at.isoformat() if cmd.processed_at else None,
            })

    # Daemon didn't pick it up — check if it's alive via heartbeat file
    # from django.conf import settings as django_settings  # moved to top-level
    heartbeat_file = Path(django_settings.BASE_DIR) / 'tmp' / 'sqlery_daemon.heartbeat'
    daemon_alive = False
    if heartbeat_file.exists():
        try:
            ts = float(heartbeat_file.read_text().strip())
            daemon_alive = (time.time() - ts) < 60
        except (ValueError, OSError):
            pass

    if not daemon_alive:
        # Daemon is down — do a direct intervention (fallback)
        cmd.delete()
        # from .intervention import do_manual_intervention_direct  # moved to top-level
        result = do_manual_intervention_direct()
        return JsonResponse({
            'status': 'completed',
            'result': result,
            'note': 'Daemon appears down — intervention ran directly from API',
        })

    return JsonResponse({
        'status': 'pending',
        'message': 'Command queued but daemon has not processed it yet',
        'command_id': cmd.id,
    }, status=202)
