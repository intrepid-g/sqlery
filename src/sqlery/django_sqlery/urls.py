"""URL patterns for sqlery internal endpoints and API."""

from django.urls import path
from django.views.generic import RedirectView
from .views import internal_worker, health_check, dashboard_stats, dump_scheduled_tasks, load_scheduled_tasks, trigger_view
from .dashboard_views import dashboard_view, sqlery_unified_view, sqlery_task_detail_view, sqlery_worker_detail_view
from .api_views import (
    api_scheduled_tasks_list,
    api_task_detail,
    api_task_jobs,
    api_task_action,
    api_stop_job,
    api_queue_jobs,
    api_worker_detail,
    api_worker_action,
    api_remove_queued_job,
    api_enqueue_job_now,
    api_job_priority,
    api_vacuum,
    api_activity_feed,
    api_clear_jobs,
    api_archive_scheduled_jobs,
    api_manual_intervention,
)

app_name = "sqlery"

urlpatterns = [
    # Internal endpoints
    path("_internal/worker", internal_worker, name="internal_worker"),
    path("_internal/health", health_check, name="health_check"),
    path("_internal/trigger", trigger_view, name="trigger"),

    # API endpoints (all return JSON for API-first architecture)
    path("admin/api/sqlery/stats/", dashboard_stats, name="api_stats"),
    path("admin/api/sqlery/tasks/", api_scheduled_tasks_list, name="api_tasks_list"),
    path("admin/api/sqlery/tasks/<int:task_id>/", api_task_detail, name="api_task_detail"),
    path("admin/api/sqlery/tasks/<int:task_id>/jobs/", api_task_jobs, name="api_task_jobs"),
    path("admin/api/sqlery/tasks/<int:task_id>/action/", api_task_action, name="api_task_action"),
    path("admin/api/sqlery/jobs/<int:job_id>/stop/", api_stop_job, name="api_stop_job"),
    path("admin/api/sqlery/workers/<str:worker_id>/", api_worker_detail, name="api_worker_detail"),
    path("admin/api/sqlery/workers/<str:worker_id>/action/", api_worker_action, name="api_worker_action"),
    path("admin/api/sqlery/jobs/<int:job_id>/remove/", api_remove_queued_job, name="api_remove_queued_job"),
    path("admin/api/sqlery/jobs/<int:job_id>/enqueue-now/", api_enqueue_job_now, name="api_enqueue_job_now"),
    path("admin/api/sqlery/jobs/<int:job_id>/priority/", api_job_priority, name="api_job_priority"),
    path("admin/api/sqlery/queues/<str:queue_name>/jobs/", api_queue_jobs, name="api_queue_jobs"),
    path("admin/api/sqlery/jobs/clear/", api_clear_jobs, name="api_clear_jobs"),
    path("admin/api/sqlery/jobs/archive-scheduled/", api_archive_scheduled_jobs, name="api_archive_scheduled_jobs"),
    path("admin/api/sqlery/vacuum/", api_vacuum, name="api_vacuum"),
    path("admin/api/sqlery/intervene/", api_manual_intervention, name="api_intervene"),
    path("admin/api/sqlery/feed/", api_activity_feed, name="api_activity_feed"),

    # Fixture dump/load (smuggler-compatible)
    path("admin/sqlery/dump-tasks/", dump_scheduled_tasks, name="dump_tasks"),
    path("admin/sqlery/load-tasks/", load_scheduled_tasks, name="load_tasks"),

    # Admin UI endpoints (HTML templates that consume APIs)
    path("admin/sqlery/", sqlery_unified_view, name="unified_view"),
    path("admin/sqlery/task/<int:task_id>/", sqlery_task_detail_view, name="task_detail"),
    path("admin/sqlery/worker/<str:worker_id>/", sqlery_worker_detail_view, name="worker_detail"),

    # Legacy dashboard (keeping for now, can be removed later)
    path("admin/dashboard/", dashboard_view, name="dashboard"),

    # Compat redirect: /admin/scheduler/ -> /admin/sqlery/ (Gotcha 6)
    path(
        "admin/scheduler/",
        RedirectView.as_view(pattern_name="sqlery:unified_view", permanent=False),
        name="scheduler_compat_redirect",
    ),
]
