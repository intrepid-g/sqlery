"""FastAPI application for sqlery standalone mode.

Provides web UI and REST API for job queue management.
"""

from datetime import datetime, UTC
from fastapi import FastAPI, Request, HTTPException, Body
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from pathlib import Path
from pydantic import BaseModel, Field

# Create FastAPI app
app = FastAPI(
    title="sqlery Dashboard",
    description="Background job queue management for Python",
    version="1.0.0",
)

# Get templates directory
TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

# Create directories if they don't exist
TEMPLATES_DIR.mkdir(exist_ok=True)
STATIC_DIR.mkdir(exist_ok=True)

# Set up Jinja2 templates
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Mount static files (if directory exists and has content)
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ============================================================================
# Request/Response Models
# ============================================================================

class CreateJobRequest(BaseModel):
    """Request model for creating a job."""
    task_path: str = Field(..., description="Python import path to task function")
    kwargs: dict = Field(default_factory=dict, description="Task arguments")
    queue_name: str = Field(default="default", description="Queue name")
    priority: int = Field(default=5, ge=0, le=10, description="Priority (0-10)")
    scheduled_at: datetime | None = Field(None, description="Schedule for future execution")
    max_retries: int = Field(default=3, ge=0, description="Maximum retry attempts")
    retry_backoff: float = Field(default=30.0, ge=0, description="Retry backoff in seconds")
    allow_parallel: bool = Field(default=True, description="Allow parallel execution")
    timeout_seconds: int | None = Field(None, ge=1, description="Task timeout in seconds")


class CreateScheduledTaskRequest(BaseModel):
    """Request model for creating a scheduled task."""
    name: str = Field(..., description="Unique task name")
    task_path: str = Field(..., description="Python import path to task function")
    cron_expression: str = Field(..., description="Cron expression for scheduling")
    queue_name: str = Field(default="default", description="Queue name")
    priority: int = Field(default=5, ge=0, le=10, description="Priority (0-10)")
    enabled: bool = Field(default=True, description="Whether task is enabled")


class UpdateScheduledTaskRequest(BaseModel):
    """Request model for updating a scheduled task."""
    name: str | None = None
    task_path: str | None = None
    cron_expression: str | None = None
    queue_name: str | None = None
    priority: int | None = Field(None, ge=0, le=10)
    enabled: bool | None = None


# ============================================================================
# Dashboard Routes
# ============================================================================

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main dashboard with overview statistics."""
    from ..compat import get_backend

    backend = get_backend()

    # Get statistics
    stats = backend.get_database_stats()
    queue_stats = backend.get_queue_stats()
    workers = backend.get_worker_heartbeats(active_only=True)

    # Get recent jobs (last 10)
    recent_jobs = backend.get_running_jobs()[:10]

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "stats": stats,
            "queue_stats": queue_stats,
            "workers": workers,
            "recent_jobs": recent_jobs,
            "page": "dashboard",
        },
    )


@app.get("/jobs", response_class=HTMLResponse)
async def jobs_list(
    request: Request,
    status: str = None,
    queue: str = None,
    page: int = 1,
    per_page: int = 50,
):
    """List all jobs with optional filters."""
    from ..compat import get_backend

    backend = get_backend()

    # Calculate offset for pagination
    offset = (page - 1) * per_page

    # Get jobs with filters and pagination
    jobs = backend.get_jobs(
        status=status,
        queue_name=queue,
        limit=per_page,
        offset=offset,
    )

    # Get total count for pagination
    total_count = backend.count_jobs(
        status=status,
        queue_name=queue,
    )

    # Calculate pagination info
    total_pages = (total_count + per_page - 1) // per_page
    has_prev = page > 1
    has_next = page < total_pages

    return templates.TemplateResponse(
        "jobs_list.html",
        {
            "request": request,
            "jobs": jobs,
            "status_filter": status,
            "queue_filter": queue,
            "page": page,
            "per_page": per_page,
            "total_count": total_count,
            "total_pages": total_pages,
            "has_prev": has_prev,
            "has_next": has_next,
            "nav_page": "jobs",
        },
    )


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail(request: Request, job_id: int):
    """Job detail view."""
    from ..compat import get_backend

    backend = get_backend()
    job = backend.get_job_by_id(job_id)

    if not job:
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "error": f"Job {job_id} not found"},
            status_code=404,
        )

    return templates.TemplateResponse(
        "job_detail.html",
        {
            "request": request,
            "job": job,
            "page": "jobs",
        },
    )


@app.get("/scheduled-tasks", response_class=HTMLResponse)
async def scheduled_tasks_list(request: Request):
    """List all scheduled tasks."""
    from ..compat import get_backend

    backend = get_backend()
    tasks = backend.get_scheduled_tasks()

    return templates.TemplateResponse(
        "scheduled_tasks.html",
        {
            "request": request,
            "tasks": tasks,
            "page": "scheduled_tasks",
        },
    )


@app.get("/workers", response_class=HTMLResponse)
async def workers_list(request: Request):
    """List all workers."""
    from ..compat import get_backend

    backend = get_backend()
    workers = backend.get_worker_heartbeats(active_only=False)

    return templates.TemplateResponse(
        "workers.html",
        {
            "request": request,
            "workers": workers,
            "page": "workers",
        },
    )


@app.get("/registries", response_class=HTMLResponse)
async def registries_view(request: Request):
    """View job registries."""
    from ..compat import get_backend

    backend = get_backend()

    # Get jobs in each registry type
    registries = {
        'started': backend.get_registry_jobs('started', limit=50),
        'finished': backend.get_registry_jobs('finished', limit=50),
        'failed': backend.get_registry_jobs('failed', limit=50),
    }

    return templates.TemplateResponse(
        "registries.html",
        {
            "request": request,
            "registries": registries,
            "page": "registries",
        },
    )


# ============================================================================
# API Routes
# ============================================================================

@app.get("/api/stats")
async def api_stats():
    """Get dashboard statistics (JSON)."""
    from ..compat import get_backend

    backend = get_backend()
    stats = backend.get_database_stats()
    queue_stats = backend.get_queue_stats()

    return {
        "database": stats,
        "queue": queue_stats,
        "timestamp": datetime.now(UTC).isoformat(),
    }


@app.get("/api/jobs")
async def api_jobs_list(
    status: str = None,
    queue: str = None,
    limit: int = 100,
    offset: int = 0,
):
    """Get jobs list (JSON)."""
    from ..compat import get_backend

    backend = get_backend()

    # Get jobs with filters and pagination
    jobs = backend.get_jobs(
        status=status,
        queue_name=queue,
        limit=limit,
        offset=offset,
    )

    # Get total count
    total_count = backend.count_jobs(
        status=status,
        queue_name=queue,
    )

    return {
        "jobs": [
            {
                "id": job.id,
                "task_path": job.task_path,
                "queue_name": job.queue_name,
                "status": job.status,
                "priority": job.priority,
                "created_at": job.created_at.isoformat() if job.created_at else None,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            }
            for job in jobs
        ],
        "count": len(jobs),
        "total": total_count,
        "offset": offset,
        "limit": limit,
    }


@app.get("/api/jobs/{job_id}")
async def api_job_detail(job_id: int):
    """Get job details (JSON)."""
    from ..compat import get_backend

    backend = get_backend()
    job = backend.get_job_by_id(job_id)

    if not job:
        return {"error": f"Job {job_id} not found"}, 404

    return {
        "id": job.id,
        "task_path": job.task_path,
        "kwargs": job.kwargs,
        "queue_name": job.queue_name,
        "status": job.status,
        "priority": job.priority,
        "retry_count": job.retry_count,
        "max_retries": job.max_retries,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "duration_seconds": job.duration_seconds,
        "output": job.output,
        "error": job.error,
        "traceback": job.traceback,
        "runs": job.runs,
    }


@app.post("/api/jobs", status_code=201)
async def api_create_job(request: CreateJobRequest):
    """Create a new job (JSON)."""
    from ..compat import get_backend

    backend = get_backend()

    try:
        job = backend.create_job(
            task_path=request.task_path,
            kwargs=request.kwargs,
            queue_name=request.queue_name,
            priority=request.priority,
            scheduled_at=request.scheduled_at,
            max_retries=request.max_retries,
            retry_backoff=request.retry_backoff,
            allow_parallel=request.allow_parallel,
            timeout_seconds=request.timeout_seconds,
        )

        return {
            "success": True,
            "job": {
                "id": job.id,
                "task_path": job.task_path,
                "queue_name": job.queue_name,
                "status": job.status,
                "priority": job.priority,
                "created_at": job.created_at.isoformat() if job.created_at else None,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/jobs/{job_id}")
async def api_cancel_job(job_id: int):
    """Cancel a job (JSON)."""
    from ..compat import get_backend

    backend = get_backend()
    success = backend.cancel_job(job_id)

    if success:
        return {"success": True, "message": f"Job {job_id} cancelled"}
    else:
        raise HTTPException(status_code=400, detail="Job not found or not in queued status")


@app.get("/api/workers")
async def api_workers_list(active_only: bool = True):
    """Get workers list (JSON)."""
    from ..compat import get_backend

    backend = get_backend()
    workers = backend.get_worker_heartbeats(active_only=active_only)

    return {
        "workers": [
            {
                "id": str(worker.id),
                "node_id": worker.node_id,
                "pid": worker.pid,
                "status": worker.status,
                "queues": worker.queues,
                "last_heartbeat": worker.last_heartbeat.isoformat() if worker.last_heartbeat else None,
                "jobs_processed": worker.jobs_processed,
            }
            for worker in workers
        ],
        "count": len(workers),
    }


@app.get("/api/scheduled-tasks")
async def api_scheduled_tasks_list():
    """Get scheduled tasks list (JSON)."""
    from ..compat import get_backend

    backend = get_backend()
    tasks = backend.get_scheduled_tasks()

    return {
        "tasks": [
            {
                "id": task.id,
                "name": task.name,
                "task_path": task.task_path,
                "cron_expression": task.cron_expression,
                "queue_name": task.queue_name,
                "priority": task.priority,
                "enabled": task.enabled,
                "last_run_at": task.last_run_at.isoformat() if task.last_run_at else None,
                "next_run_at": task.next_run_at.isoformat() if task.next_run_at else None,
            }
            for task in tasks
        ],
        "count": len(tasks),
    }


@app.post("/api/scheduled-tasks", status_code=201)
async def api_create_scheduled_task(request: CreateScheduledTaskRequest):
    """Create a new scheduled task (JSON)."""
    from ..compat import get_backend

    backend = get_backend()

    try:
        task = backend.create_scheduled_task(
            name=request.name,
            task_path=request.task_path,
            cron_expression=request.cron_expression,
            queue_name=request.queue_name,
            priority=request.priority,
            enabled=request.enabled,
        )

        return {
            "success": True,
            "task": {
                "id": task.id,
                "name": task.name,
                "task_path": task.task_path,
                "cron_expression": task.cron_expression,
                "queue_name": task.queue_name,
                "priority": task.priority,
                "enabled": task.enabled,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/scheduled-tasks/{task_id}")
async def api_scheduled_task_detail(task_id: int):
    """Get scheduled task details (JSON)."""
    from ..compat import get_backend

    backend = get_backend()
    task = backend.get_scheduled_task(task_id)

    if not task:
        raise HTTPException(status_code=404, detail=f"Scheduled task {task_id} not found")

    return {
        "id": task.id,
        "name": task.name,
        "task_path": task.task_path,
        "cron_expression": task.cron_expression,
        "queue_name": task.queue_name,
        "priority": task.priority,
        "enabled": task.enabled,
        "last_run_at": task.last_run_at.isoformat() if task.last_run_at else None,
        "next_run_at": task.next_run_at.isoformat() if task.next_run_at else None,
        "created_at": task.created_at.isoformat() if task.created_at else None,
    }


@app.put("/api/scheduled-tasks/{task_id}")
@app.patch("/api/scheduled-tasks/{task_id}")
async def api_update_scheduled_task(task_id: int, request: UpdateScheduledTaskRequest):
    """Update a scheduled task (JSON)."""
    from ..compat import get_backend

    backend = get_backend()

    # Check if task exists
    task = backend.get_scheduled_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Scheduled task {task_id} not found")

    try:
        # Build update dict with only provided fields
        updates = {}
        if request.name is not None:
            updates['name'] = request.name
        if request.task_path is not None:
            updates['task_path'] = request.task_path
        if request.cron_expression is not None:
            updates['cron_expression'] = request.cron_expression
        if request.queue_name is not None:
            updates['queue_name'] = request.queue_name
        if request.priority is not None:
            updates['priority'] = request.priority
        if request.enabled is not None:
            updates['enabled'] = request.enabled

        # Update task
        updated_task = backend.update_scheduled_task(task_id, **updates)

        return {
            "success": True,
            "task": {
                "id": updated_task.id,
                "name": updated_task.name,
                "task_path": updated_task.task_path,
                "cron_expression": updated_task.cron_expression,
                "queue_name": updated_task.queue_name,
                "priority": updated_task.priority,
                "enabled": updated_task.enabled,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/scheduled-tasks/{task_id}")
async def api_delete_scheduled_task(task_id: int):
    """Delete a scheduled task (JSON)."""
    from ..compat import get_backend

    backend = get_backend()
    success = backend.delete_scheduled_task(task_id)

    if success:
        return {"success": True, "message": f"Scheduled task {task_id} deleted"}
    else:
        raise HTTPException(status_code=404, detail="Scheduled task not found")


# ============================================================================
# Health Check
# ============================================================================

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    from ..compat import is_standalone_mode

    return {
        "status": "healthy",
        "mode": "standalone" if is_standalone_mode() else "django",
        "timestamp": datetime.now(UTC).isoformat(),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
