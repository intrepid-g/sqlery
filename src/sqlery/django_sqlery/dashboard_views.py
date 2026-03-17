"""Dashboard views for Sqlery admin."""

from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render, get_object_or_404
from .models import ScheduledTask, Worker


@staff_member_required
def dashboard_view(request):
    """Dashboard page view (requires staff permission)."""
    from django.contrib import admin

    context = {
        'title': 'Sqlery Dashboard',
        'site_title': admin.site.site_title,
        'site_header': admin.site.site_header,
        'has_permission': request.user.is_active and request.user.is_staff,
    }
    return render(request, 'admin/sqlery/dashboard.html', context)


@staff_member_required
def sqlery_unified_view(request):
    """Main unified SQLery admin page - renders template that consumes APIs.

    This is the single entry point for SQLery admin, showing:
    - Live dashboard stats (queued, running, success, failed)
    - Scheduled tasks table
    - Recent jobs

    All data is fetched via JavaScript from API endpoints for easier iteration.
    """
    from django.contrib import admin

    context = {
        'title': 'SQLery - Task Queue Management',
        'site_title': admin.site.site_title,
        'site_header': admin.site.site_header,
        'has_permission': request.user.is_active and request.user.is_staff,
    }
    return render(request, 'admin/sqlery/unified_dashboard.html', context)


@staff_member_required
def sqlery_task_detail_view(request, task_id):
    """Task detail page - shows job runs for specific task.

    Args:
        task_id: Primary key of the ScheduledTask

    Shows:
    - Task metadata (name, cron, queue, priority, etc.)
    - Latest run highlighted
    - Job statistics
    - Paginated job history with output/errors

    All job data is fetched via JavaScript from API endpoints.
    """
    from django.contrib import admin

    task = get_object_or_404(ScheduledTask, id=task_id)

    context = {
        'title': f'Task: {task.name}',
        'site_title': admin.site.site_title,
        'site_header': admin.site.site_header,
        'has_permission': request.user.is_active and request.user.is_staff,
        'task_id': task_id,
        'task_name': task.name,
    }
    return render(request, 'admin/sqlery/task_detail.html', context)


@staff_member_required
def sqlery_worker_detail_view(request, worker_id):
    """Worker detail page - shows worker info and job history."""
    import uuid
    from django.contrib import admin

    worker = get_object_or_404(Worker, id=uuid.UUID(worker_id))

    context = {
        'title': f'Worker: {str(worker.id)[:8]}',
        'site_title': admin.site.site_title,
        'site_header': admin.site.site_header,
        'has_permission': request.user.is_active and request.user.is_staff,
        'worker_id': str(worker.id),
    }
    return render(request, 'admin/sqlery/worker_detail.html', context)
