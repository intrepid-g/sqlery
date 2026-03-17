"""Django-agnostic CLI for sqlery (Typer-based).

Provides command-line interface for standalone mode.
Django mode uses Django management commands instead.
"""

import sys
import typer
from rich.console import Console
from rich.table import Table
# from typing import Optional  # Replaced with X | None (Python 3.10+)

app = typer.Typer(
    name="sqlery",
    help="Sqlery - Background job queue for Django and standalone Python",
    no_args_is_help=True,
)

console = Console()


# ============================================================================
# Daemon Commands
# ============================================================================

daemon_app = typer.Typer(help="Daemon management commands")
app.add_typer(daemon_app, name="daemon")


@daemon_app.command("start")
def daemon_start(
    detach: bool = typer.Option(True, "--detach/--no-detach", help="Run daemon in background"),
):
    """Start the job processing daemon."""
    from ..compat import is_django_mode

    if is_django_mode():
        console.print("[red]Error: Use 'python manage.py daemon start' in Django mode[/red]")
        raise typer.Exit(1)

    from .daemon import DaemonManager

    console.print("[bold blue]Starting sqlery daemon...[/bold blue]")

    daemon = DaemonManager()
    try:
        daemon.start(detach=detach)
        if detach:
            console.print("[green]✓[/green] Daemon started successfully")
        # If not detached, this will block until daemon stops
    except Exception as e:
        console.print(f"[red]✗ Failed to start daemon: {e}[/red]")
        raise typer.Exit(1)


@daemon_app.command("stop")
def daemon_stop():
    """Stop the job processing daemon."""
    from ..compat import is_django_mode

    if is_django_mode():
        console.print("[red]Error: Use 'python manage.py daemon stop' in Django mode[/red]")
        raise typer.Exit(1)

    from .daemon import DaemonManager

    console.print("[bold blue]Stopping sqlery daemon...[/bold blue]")

    daemon = DaemonManager()
    try:
        daemon.stop()
        console.print("[green]✓[/green] Daemon stopped successfully")
    except Exception as e:
        console.print(f"[red]✗ Failed to stop daemon: {e}[/red]")
        raise typer.Exit(1)


@daemon_app.command("restart")
def daemon_restart():
    """Restart the job processing daemon."""
    from ..compat import is_django_mode

    if is_django_mode():
        console.print("[red]Error: Use 'python manage.py daemon restart' in Django mode[/red]")
        raise typer.Exit(1)

    from .daemon import DaemonManager

    console.print("[bold blue]Restarting sqlery daemon...[/bold blue]")

    daemon = DaemonManager()
    try:
        daemon.restart()
        console.print("[green]✓[/green] Daemon restarted successfully")
    except Exception as e:
        console.print(f"[red]✗ Failed to restart daemon: {e}[/red]")
        raise typer.Exit(1)


@daemon_app.command("status")
def daemon_status():
    """Check daemon status."""
    from ..compat import is_django_mode

    if is_django_mode():
        console.print("[red]Error: Use 'python manage.py daemon status' in Django mode[/red]")
        raise typer.Exit(1)

    from .daemon import DaemonManager

    daemon = DaemonManager()
    status = daemon.status()

    if status["running"]:
        console.print(f"[green]●[/green] Daemon is [bold green]RUNNING[/bold green]")
        console.print(f"  PID: {status['pid']}")
        console.print(f"  Uptime: {status.get('uptime', 'unknown')}")
        console.print(f"  Workers: {status.get('worker_count', 0)}")
    else:
        console.print(f"[red]●[/red] Daemon is [bold red]STOPPED[/bold red]")


# ============================================================================
# Worker Commands
# ============================================================================

workers_app = typer.Typer(help="Worker management commands")
app.add_typer(workers_app, name="workers")


@workers_app.command("list")
def workers_list(
    active_only: bool = typer.Option(True, "--active-only/--all", help="Show only active workers"),
):
    """List all workers."""
    from ..compat import is_django_mode, get_backend

    if is_django_mode():
        console.print("[red]Error: Use 'python manage.py workers list' in Django mode[/red]")
        raise typer.Exit(1)

    backend = get_backend()
    workers = backend.get_worker_heartbeats(active_only=active_only)

    if not workers:
        console.print("[yellow]No workers found[/yellow]")
        return

    table = Table(title="Sqlery Workers")
    table.add_column("ID", style="cyan")
    table.add_column("Node", style="magenta")
    table.add_column("PID", style="green")
    table.add_column("Status", style="yellow")
    table.add_column("Last Heartbeat", style="blue")
    table.add_column("Jobs Processed", style="white")

    for worker in workers:
        table.add_row(
            str(worker['id'])[:8],
            str(worker.get('node_id', '')),
            str(worker.get('pid', '')),
            str(worker.get('status', '')),
            str(worker.get('last_heartbeat', '')),
            str(worker.get('jobs_processed', 0)),
        )

    console.print(table)


@workers_app.command("stop")
def workers_stop(
    worker_id: str | None = typer.Option(None, "--worker-id", "-w", help="Stop specific worker by ID"),
):
    """Stop all workers gracefully (or a specific worker by ID)."""
    from ..compat import is_django_mode, get_backend
    import signal
    import os

    if is_django_mode():
        console.print("[red]Error: Use 'python manage.py workers stop' in Django mode[/red]")
        raise typer.Exit(1)

    backend = get_backend()
    workers = backend.get_worker_heartbeats(active_only=True)

    if not workers:
        console.print("[yellow]No active workers found[/yellow]")
        return

    # Filter by worker_id if specified
    if worker_id:
        workers = [w for w in workers if str(w['id']).startswith(worker_id)]
        if not workers:
            console.print(f"[red]Worker {worker_id} not found[/red]")
            raise typer.Exit(1)

    console.print(f"[bold blue]Stopping {len(workers)} worker(s) gracefully...[/bold blue]")

    stopped_count = 0
    failed_count = 0

    for worker in workers:
        worker_pid = worker['pid']
        worker_id_str = str(worker['id'])[:8]

        try:
            # Send SIGTERM for graceful shutdown
            os.kill(worker_pid, signal.SIGTERM)
            console.print(f"[green]✓[/green] Sent SIGTERM to worker {worker_id_str} (PID: {worker_pid})")
            stopped_count += 1
        except ProcessLookupError:
            console.print(f"[yellow]⚠[/yellow] Worker {worker_id_str} (PID: {worker_pid}) not found - already stopped")
            failed_count += 1
        except PermissionError:
            console.print(f"[red]✗[/red] Permission denied to stop worker {worker_id_str} (PID: {worker_pid})")
            failed_count += 1
        except Exception as e:
            console.print(f"[red]✗[/red] Failed to stop worker {worker_id_str}: {e}")
            failed_count += 1

    console.print(f"\n[bold]Summary:[/bold] {stopped_count} stopped, {failed_count} failed")


@workers_app.command("kill")
def workers_kill(
    worker_id: str | None = typer.Option(None, "--worker-id", "-w", help="Kill specific worker by ID"),
    force: bool = typer.Option(False, "--force", "-f", help="Confirm force kill"),
):
    """Force kill workers (SIGKILL). Use with caution!"""
    from ..compat import is_django_mode, get_backend
    import signal
    import os

    if is_django_mode():
        console.print("[red]Error: Use 'python manage.py workers kill' in Django mode[/red]")
        raise typer.Exit(1)

    if not force:
        console.print("[yellow]⚠ This will force kill workers (SIGKILL) - jobs may be left incomplete![/yellow]")
        console.print("[yellow]Use --force to confirm[/yellow]")
        raise typer.Exit(1)

    backend = get_backend()
    workers = backend.get_worker_heartbeats(active_only=True)

    if not workers:
        console.print("[yellow]No active workers found[/yellow]")
        return

    # Filter by worker_id if specified
    if worker_id:
        workers = [w for w in workers if str(w['id']).startswith(worker_id)]
        if not workers:
            console.print(f"[red]Worker {worker_id} not found[/red]")
            raise typer.Exit(1)

    console.print(f"[bold red]Force killing {len(workers)} worker(s)...[/bold red]")

    killed_count = 0
    failed_count = 0

    for worker in workers:
        worker_pid = worker['pid']
        worker_id_str = str(worker['id'])[:8]

        try:
            # Send SIGKILL for immediate termination
            os.kill(worker_pid, signal.SIGKILL)
            console.print(f"[green]✓[/green] Sent SIGKILL to worker {worker_id_str} (PID: {worker_pid})")
            killed_count += 1
        except ProcessLookupError:
            console.print(f"[yellow]⚠[/yellow] Worker {worker_id_str} (PID: {worker_pid}) not found - already stopped")
            failed_count += 1
        except PermissionError:
            console.print(f"[red]✗[/red] Permission denied to kill worker {worker_id_str} (PID: {worker_pid})")
            failed_count += 1
        except Exception as e:
            console.print(f"[red]✗[/red] Failed to kill worker {worker_id_str}: {e}")
            failed_count += 1

    console.print(f"\n[bold]Summary:[/bold] {killed_count} killed, {failed_count} failed")


@workers_app.command("cleanup")
def workers_cleanup(
    max_age_hours: int = typer.Option(1, "--max-age-hours", "-a", help="Remove workers inactive for N hours"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be deleted without deleting"),
):
    """Clean up stale worker records from database."""
    from ..compat import is_django_mode, get_backend
    from datetime import timedelta

    if is_django_mode():
        console.print("[red]Error: Use 'python manage.py workers cleanup' in Django mode[/red]")
        raise typer.Exit(1)

    backend = get_backend()

    # Get stale workers (no heartbeat in N hours)
    all_workers = backend.get_worker_heartbeats(active_only=False)

    from datetime import datetime, UTC
    cutoff = datetime.now(UTC) - timedelta(hours=max_age_hours)

    stale_workers = [
        w for w in all_workers
        if w['last_heartbeat'] and datetime.fromisoformat(str(w['last_heartbeat'])).replace(tzinfo=UTC) < cutoff
    ]

    if not stale_workers:
        console.print(f"[green]✓[/green] No stale workers found (inactive > {max_age_hours}h)")
        return

    console.print(f"[bold blue]Found {len(stale_workers)} stale worker(s){'(dry run)' if dry_run else ''}:[/bold blue]")

    for worker in stale_workers:
        worker_id_str = str(worker['id'])[:8]
        last_seen = worker['last_heartbeat']
        console.print(f"  - {worker_id_str} (PID: {worker['pid']}, last seen: {last_seen})")

    if dry_run:
        console.print(f"\n[yellow]Dry run - no workers deleted[/yellow]")
        return

    # Delete stale workers by updating status to 'dead', then clean via cleanup_jobs
    deleted_count = 0
    for worker in stale_workers:
        try:
            backend.update_worker_heartbeat(
                worker_id=str(worker['id']),
                status='dead',
            )
            deleted_count += 1
        except Exception as e:
            console.print(f"[red]✗[/red] Failed to clean up worker {worker['id']}: {e}")

    console.print(f"\n[green]✓[/green] Cleaned up {deleted_count} stale worker record(s)")


# ============================================================================
# Cleanup Commands
# ============================================================================

cleanup_app = typer.Typer(help="Database cleanup commands")
app.add_typer(cleanup_app, name="cleanup")


@cleanup_app.command("auto")
def cleanup_auto(
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be deleted without deleting"),
):
    """Run automatic cleanup based on configuration."""
    from ..compat import is_django_mode
    from .cleanup import CleanupManager

    if is_django_mode():
        console.print("[red]Error: Use 'python manage.py cleanup_jobs auto' in Django mode[/red]")
        raise typer.Exit(1)

    console.print(f"[bold blue]Running automatic cleanup{'(dry run)' if dry_run else ''}...[/bold blue]")

    manager = CleanupManager()
    results = manager.auto_cleanup(dry_run=dry_run)

    console.print(f"\n[green]✓[/green] Cleanup completed at {results['timestamp']}")
    console.print(f"  Actions performed: {len(results['actions'])}")

    for action in results['actions']:
        action_type = action['action']
        result = action['result']
        deleted = result.get('deleted', 0) or result.get('would_delete', 0)
        console.print(f"  - {action_type}: {deleted} items")


@cleanup_app.command("stats")
def cleanup_stats():
    """Show database statistics."""
    from ..compat import is_django_mode
    from .cleanup import CleanupManager

    if is_django_mode():
        console.print("[red]Error: Use 'python manage.py cleanup_jobs stats' in Django mode[/red]")
        raise typer.Exit(1)

    manager = CleanupManager()
    stats = manager.get_database_stats()

    console.print("[bold blue]Database Statistics[/bold blue]")
    console.print(f"\n[cyan]Jobs:[/cyan]")
    console.print(f"  Total: {stats.get('total_jobs', 0)}")
    for status, count in stats.get('job_counts', {}).items():
        console.print(f"  {status}: {count}")

    console.print(f"\n[cyan]Registries:[/cyan]")
    console.print(f"  Total: {stats.get('total_registries', 0)}")
    for registry_type, count in stats.get('registry_counts', {}).items():
        console.print(f"  {registry_type}: {count}")


@cleanup_app.command("vacuum")
def cleanup_vacuum():
    """Run database vacuum/optimize (PostgreSQL only)."""
    from ..compat import is_django_mode
    from .cleanup import CleanupManager

    if is_django_mode():
        console.print("[red]Error: Use 'python manage.py cleanup_jobs vacuum' in Django mode[/red]")
        raise typer.Exit(1)

    console.print("[bold blue]Running database vacuum...[/bold blue]")

    manager = CleanupManager()
    result = manager.vacuum_database()

    if result['success']:
        console.print(f"[green]✓[/green] {result['message']}")
    else:
        console.print(f"[red]✗[/red] {result.get('error', result.get('message', 'Unknown error'))}")
        raise typer.Exit(1)


# ============================================================================
# Job Commands
# ============================================================================

jobs_app = typer.Typer(help="Job management commands")
app.add_typer(jobs_app, name="jobs")


@jobs_app.command("list")
def jobs_list(
    queue: str | None = typer.Option(None, "--queue", "-q", help="Filter by queue name"),
    status: str | None = typer.Option(None, "--status", "-s", help="Filter by status"),
    limit: int = typer.Option(10, "--limit", "-n", help="Number of jobs to show"),
):
    """List jobs."""
    from ..compat import is_django_mode, get_backend

    if is_django_mode():
        console.print("[red]Error: Use Django admin to view jobs in Django mode[/red]")
        raise typer.Exit(1)

    backend = get_backend()

    try:
        jobs = backend.get_jobs(
            status=status,
            queue_name=queue,
            limit=limit,
        )
    except Exception as e:
        console.print(f"[red]Failed to fetch jobs: {e}[/red]")
        raise typer.Exit(1)

    if not jobs:
        console.print("[yellow]No jobs found[/yellow]")
        return

    table = Table(title="Sqlery Jobs")
    table.add_column("ID", style="cyan")
    table.add_column("Task", style="magenta")
    table.add_column("Queue", style="green")
    table.add_column("Status", style="yellow")
    table.add_column("Priority", style="white")
    table.add_column("Created At", style="blue")
    table.add_column("Retries", style="white")

    for job in jobs:
        job_status = str(job.get("status", ""))
        status_style = {
            "queued": "[yellow]queued[/yellow]",
            "running": "[blue]running[/blue]",
            "success": "[green]success[/green]",
            "failed": "[red]failed[/red]",
        }.get(job_status, job_status)

        table.add_row(
            str(job.get("id", ""))[:8],
            str(job.get("task_path", "")),
            str(job.get("queue_name", "default")),
            status_style,
            str(job.get("priority", 0)),
            str(job.get("created_at", "")),
            str(job.get("retry_count", 0)),
        )

    console.print(table)
    console.print(f"\n[dim]Showing {len(jobs)} job(s)[/dim]")


@jobs_app.command("inspect")
def jobs_inspect(
    job_id: int = typer.Argument(..., help="Job ID to inspect"),
):
    """Show detailed information about a specific job."""
    from ..compat import is_django_mode, get_backend

    if is_django_mode():
        console.print("[red]Error: Use Django admin to view jobs in Django mode[/red]")
        raise typer.Exit(1)

    backend = get_backend()

    try:
        job = backend.get_job_by_id(job_id)
    except Exception as e:
        console.print(f"[red]Failed to fetch job: {e}[/red]")
        raise typer.Exit(1)

    if not job:
        console.print(f"[red]Job {job_id} not found[/red]")
        raise typer.Exit(1)

    job_status = str(job.get("status", ""))
    status_color = {"queued": "yellow", "running": "blue", "success": "green", "failed": "red", "cancelled": "dim"}.get(job_status, "white")

    console.print(f"\n[bold]Job {job.get('id')}[/bold]")
    console.print(f"  Task:           {job.get('task_path', '')}")
    console.print(f"  Queue:          {job.get('queue_name', 'default')}")
    console.print(f"  Status:         [{status_color}]{job_status}[/{status_color}]")
    console.print(f"  Priority:       {job.get('priority', 0)}")
    console.print(f"  Created:        {job.get('created_at', '')}")
    console.print(f"  Started:        {job.get('started_at', '') or '-'}")
    console.print(f"  Finished:       {job.get('finished_at', '') or '-'}")
    console.print(f"  Duration:       {job.get('duration_seconds', '') or '-'}s")
    console.print(f"  Retries:        {job.get('retry_count', 0)} / {job.get('max_retries', 0)}")
    console.print(f"  Worker:         {str(job.get('worker_id', '') or '-')[:8]}")
    console.print(f"  Allow Parallel: {job.get('allow_parallel', False)}")
    console.print(f"  Timeout:        {job.get('timeout_seconds', '') or '-'}s")

    kwargs = job.get("kwargs", "{}")
    console.print(f"  Args:           {kwargs}")

    if job.get("output"):
        console.print(f"\n  [cyan]Output:[/cyan]\n  {job['output'][:500]}")

    if job.get("error"):
        console.print(f"\n  [red]Error:[/red]\n  {job['error']}")

    if job.get("traceback"):
        console.print(f"\n  [red]Traceback:[/red]\n  {job['traceback'][:1000]}")


@jobs_app.command("cancel")
def jobs_cancel(
    job_id: int = typer.Argument(..., help="Job ID to cancel"),
):
    """Cancel a queued job."""
    from ..compat import is_django_mode, get_backend

    if is_django_mode():
        console.print("[red]Error: Use Django admin to manage jobs in Django mode[/red]")
        raise typer.Exit(1)

    backend = get_backend()

    try:
        cancelled = backend.cancel_job(job_id)
    except Exception as e:
        console.print(f"[red]Failed to cancel job: {e}[/red]")
        raise typer.Exit(1)

    if cancelled:
        console.print(f"[green]✓[/green] Job {job_id} cancelled")
    else:
        console.print(f"[yellow]Job {job_id} not cancelled (not found or not in 'queued' status)[/yellow]")


@jobs_app.command("retry")
def jobs_retry(
    queue: str | None = typer.Option(None, "--queue", "-q", help="Retry failed jobs in specific queue"),
    limit: int | None = typer.Option(None, "--limit", "-n", help="Maximum number of jobs to retry"),
):
    """Retry failed jobs."""
    from ..compat import is_django_mode, get_backend

    if is_django_mode():
        console.print("[red]Error: Use Django admin to manage jobs in Django mode[/red]")
        raise typer.Exit(1)

    backend = get_backend()

    try:
        retried = backend.retry_failed_jobs(queue_name=queue, max_jobs=limit)
    except Exception as e:
        console.print(f"[red]Failed to retry jobs: {e}[/red]")
        raise typer.Exit(1)

    if retried:
        console.print(f"[green]✓[/green] Retried {retried} failed job(s)")
    else:
        console.print("[yellow]No eligible failed jobs to retry[/yellow]")


# ============================================================================
# Scheduled Tasks Commands
# ============================================================================

tasks_app = typer.Typer(help="Scheduled task management commands")
app.add_typer(tasks_app, name="tasks")


@tasks_app.command("list")
def tasks_list(
    enabled_only: bool = typer.Option(False, "--enabled-only", help="Show only enabled tasks"),
):
    """List all scheduled tasks."""
    from ..compat import is_django_mode, get_backend

    if is_django_mode():
        console.print("[red]Error: Use 'python manage.py run_scheduled_tasks' in Django mode[/red]")
        raise typer.Exit(1)

    backend = get_backend()

    try:
        tasks = backend.get_scheduled_tasks(enabled_only=enabled_only)
    except Exception as e:
        console.print(f"[red]Failed to fetch tasks: {e}[/red]")
        raise typer.Exit(1)

    if not tasks:
        console.print("[yellow]No scheduled tasks found[/yellow]")
        return

    table = Table(title="Scheduled Tasks")
    table.add_column("ID", style="cyan")
    table.add_column("Name", style="magenta")
    table.add_column("Task Path", style="white")
    table.add_column("Cron", style="green")
    table.add_column("Queue", style="blue")
    table.add_column("Priority", style="white")
    table.add_column("Enabled", style="yellow")
    table.add_column("Next Run", style="blue")
    table.add_column("Last Run", style="dim")

    for task in tasks:
        enabled = "[green]yes[/green]" if task.get("enabled") else "[red]no[/red]"
        table.add_row(
            str(task.get("id", "")),
            str(task.get("name", "")),
            str(task.get("task_path", "")),
            str(task.get("cron_expression", "")),
            str(task.get("queue_name", "default")),
            str(task.get("priority", 0)),
            enabled,
            str(task.get("next_run_at", "") or "-"),
            str(task.get("last_run_at", "") or "-"),
        )

    console.print(table)
    console.print(f"\n[dim]Showing {len(tasks)} task(s)[/dim]")


@tasks_app.command("create")
def tasks_create(
    name: str = typer.Option(..., "--name", "-n", help="Task name"),
    task_path: str = typer.Option(..., "--task-path", "-t", help="Python dotted path to task function"),
    cron: str = typer.Option(..., "--cron", "-c", help="Cron expression (e.g. '*/5 * * * *')"),
    queue: str = typer.Option("default", "--queue", "-q", help="Queue name"),
    priority: int = typer.Option(0, "--priority", "-p", help="Task priority"),
    disabled: bool = typer.Option(False, "--disabled", help="Create task in disabled state"),
):
    """Create a new scheduled task."""
    from ..compat import is_django_mode, get_backend

    if is_django_mode():
        console.print("[red]Error: Use Django admin to manage scheduled tasks in Django mode[/red]")
        raise typer.Exit(1)

    # Validate cron expression
    try:
        from ..crontab import parse_cron_string
        parse_cron_string(cron)
    except Exception as e:
        console.print(f"[red]Invalid cron expression '{cron}': {e}[/red]")
        raise typer.Exit(1)

    backend = get_backend()

    try:
        task = backend.create_scheduled_task(
            name=name,
            task_path=task_path,
            cron_expression=cron,
            queue_name=queue,
            priority=priority,
            enabled=not disabled,
        )
    except Exception as e:
        console.print(f"[red]Failed to create task: {e}[/red]")
        raise typer.Exit(1)

    console.print(f"[green]✓[/green] Created scheduled task '{name}' (ID: {task.get('id', '?')})")
    console.print(f"  Cron: {cron}")
    console.print(f"  Task: {task_path}")
    console.print(f"  Queue: {queue}")


@tasks_app.command("update")
def tasks_update(
    task_id: int = typer.Argument(..., help="Task ID to update"),
    name: str | None = typer.Option(None, "--name", "-n", help="New task name"),
    task_path: str | None = typer.Option(None, "--task-path", "-t", help="New task function path"),
    cron: str | None = typer.Option(None, "--cron", "-c", help="New cron expression"),
    queue: str | None = typer.Option(None, "--queue", "-q", help="New queue name"),
    priority: int | None = typer.Option(None, "--priority", "-p", help="New priority"),
    enable: bool | None = typer.Option(None, "--enable/--disable", help="Enable or disable the task"),
):
    """Update a scheduled task."""
    from ..compat import is_django_mode, get_backend

    if is_django_mode():
        console.print("[red]Error: Use Django admin to manage scheduled tasks in Django mode[/red]")
        raise typer.Exit(1)

    if cron is not None:
        try:
            from ..crontab import parse_cron_string
            parse_cron_string(cron)
        except Exception as e:
            console.print(f"[red]Invalid cron expression '{cron}': {e}[/red]")
            raise typer.Exit(1)

    backend = get_backend()

    try:
        task = backend.update_scheduled_task(
            task_id=task_id,
            name=name,
            task_path=task_path,
            cron_expression=cron,
            queue_name=queue,
            priority=priority,
            enabled=enable,
        )
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Failed to update task: {e}[/red]")
        raise typer.Exit(1)

    console.print(f"[green]✓[/green] Updated scheduled task {task_id}")


@tasks_app.command("delete")
def tasks_delete(
    task_id: int = typer.Argument(..., help="Task ID to delete"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Delete a scheduled task."""
    from ..compat import is_django_mode, get_backend

    if is_django_mode():
        console.print("[red]Error: Use Django admin to manage scheduled tasks in Django mode[/red]")
        raise typer.Exit(1)

    if not force:
        console.print(f"[yellow]This will permanently delete task {task_id}. Use --force to confirm.[/yellow]")
        raise typer.Exit(1)

    backend = get_backend()

    try:
        deleted = backend.delete_scheduled_task(task_id)
    except Exception as e:
        console.print(f"[red]Failed to delete task: {e}[/red]")
        raise typer.Exit(1)

    if deleted:
        console.print(f"[green]✓[/green] Deleted scheduled task {task_id}")
    else:
        console.print(f"[yellow]Task {task_id} not found[/yellow]")


@tasks_app.command("run")
def tasks_run(
    task_name: str | None = typer.Option(None, "--name", "-n", help="Run specific task by name"),
):
    """Trigger due scheduled tasks (or a specific task by name)."""
    from ..compat import is_django_mode, get_backend
    from ..triggers import trigger_due_tasks

    if is_django_mode():
        console.print("[red]Error: Use 'python manage.py run_scheduled_tasks' in Django mode[/red]")
        raise typer.Exit(1)

    if task_name:
        backend = get_backend()
        tasks = backend.get_scheduled_tasks()
        matching = [t for t in tasks if t.get("name") == task_name]

        if not matching:
            console.print(f"[red]Task '{task_name}' not found[/red]")
            raise typer.Exit(1)

        task = matching[0]
        if not task.get("enabled"):
            console.print(f"[yellow]Task '{task_name}' is disabled[/yellow]")
            raise typer.Exit(1)

        console.print(f"[bold blue]Running task '{task_name}'...[/bold blue]")
        try:
            trigger_due_tasks()
            console.print(f"[green]✓[/green] Task '{task_name}' triggered")
        except Exception as e:
            console.print(f"[red]Failed to run task: {e}[/red]")
            raise typer.Exit(1)
    else:
        console.print("[bold blue]Checking for due scheduled tasks...[/bold blue]")
        try:
            trigger_due_tasks()
            console.print(f"[green]✓[/green] Due tasks triggered")
        except Exception as e:
            console.print(f"[red]Failed to trigger tasks: {e}[/red]")
            raise typer.Exit(1)


# ============================================================================
# Queue Commands
# ============================================================================

queues_app = typer.Typer(help="Queue management commands")
app.add_typer(queues_app, name="queues")


@queues_app.command("list")
def queues_list():
    """List all queues with job counts."""
    from ..compat import is_django_mode, get_backend

    if is_django_mode():
        console.print("[red]Error: Use Django admin to view queues in Django mode[/red]")
        raise typer.Exit(1)

    backend = get_backend()

    try:
        jobs = backend.get_jobs(limit=10000)
    except Exception as e:
        console.print(f"[red]Failed to fetch queues: {e}[/red]")
        raise typer.Exit(1)

    # Aggregate by queue name
    queue_stats: dict[str, dict[str, int]] = {}
    for job in jobs:
        qname = job.get("queue_name", "default")
        status = job.get("status", "unknown")
        if qname not in queue_stats:
            queue_stats[qname] = {"queued": 0, "running": 0, "success": 0, "failed": 0, "cancelled": 0, "total": 0}
        queue_stats[qname][status] = queue_stats[qname].get(status, 0) + 1
        queue_stats[qname]["total"] += 1

    if not queue_stats:
        console.print("[yellow]No queues found (no jobs in database)[/yellow]")
        return

    table = Table(title="Queues")
    table.add_column("Queue", style="cyan")
    table.add_column("Queued", style="yellow")
    table.add_column("Running", style="blue")
    table.add_column("Success", style="green")
    table.add_column("Failed", style="red")
    table.add_column("Cancelled", style="dim")
    table.add_column("Total", style="bold")

    for qname in sorted(queue_stats.keys()):
        s = queue_stats[qname]
        table.add_row(
            qname,
            str(s["queued"]),
            str(s["running"]),
            str(s["success"]),
            str(s["failed"]),
            str(s["cancelled"]),
            str(s["total"]),
        )

    console.print(table)


@queues_app.command("stats")
def queues_stats(
    queue: str | None = typer.Option(None, "--queue", "-q", help="Show stats for specific queue"),
):
    """Show detailed queue statistics."""
    from ..compat import is_django_mode, get_backend

    if is_django_mode():
        console.print("[red]Error: Use Django admin to view queue stats in Django mode[/red]")
        raise typer.Exit(1)

    backend = get_backend()

    try:
        stats = backend.get_queue_stats(queue_name=queue)
    except Exception as e:
        console.print(f"[red]Failed to fetch stats: {e}[/red]")
        raise typer.Exit(1)

    title = f"Queue Stats: {queue}" if queue else "Queue Stats (all queues)"
    console.print(f"\n[bold]{title}[/bold]")

    total = sum(stats.values())
    console.print(f"  [yellow]Queued:[/yellow]    {stats.get('queued', 0)}")
    console.print(f"  [blue]Running:[/blue]   {stats.get('running', 0)}")
    console.print(f"  [green]Success:[/green]   {stats.get('success', 0)}")
    console.print(f"  [red]Failed:[/red]    {stats.get('failed', 0)}")
    console.print(f"  [dim]Cancelled:[/dim] {stats.get('cancelled', 0)}")
    console.print(f"  [bold]Total:[/bold]     {total}")


# ============================================================================
# Migration Commands (Alembic)
# ============================================================================

migrate_app = typer.Typer(help="Database migration commands (Alembic)")
app.add_typer(migrate_app, name="migrate")


@migrate_app.command("upgrade")
def migrate_upgrade(
    revision: str = typer.Argument("head", help="Target revision (default: head)"),
):
    """Run database migrations (upgrade to target revision)."""
    from ..compat import is_django_mode
    import os

    if is_django_mode():
        console.print("[red]Error: Use Django migrations in Django mode[/red]")
        raise typer.Exit(1)

    console.print(f"[bold blue]Running migrations to {revision}...[/bold blue]")

    try:
        from alembic.config import Config
        from alembic import command

        # Get the project root (where alembic.ini lives)
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        alembic_ini = os.path.join(project_root, "alembic.ini")

        if not os.path.exists(alembic_ini):
            console.print(f"[red]✗ alembic.ini not found at {alembic_ini}[/red]")
            raise typer.Exit(1)

        alembic_cfg = Config(alembic_ini)
        command.upgrade(alembic_cfg, revision)

        console.print(f"[green]✓[/green] Migrations completed successfully")
    except ImportError:
        console.print("[red]✗ Alembic not installed. Install with: pip install sqlery[standalone][/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]✗ Migration failed: {e}[/red]")
        raise typer.Exit(1)


@migrate_app.command("downgrade")
def migrate_downgrade(
    revision: str = typer.Argument("-1", help="Target revision (default: -1)"),
):
    """Rollback database migrations (downgrade to target revision)."""
    from ..compat import is_django_mode
    import os

    if is_django_mode():
        console.print("[red]Error: Use Django migrations in Django mode[/red]")
        raise typer.Exit(1)

    console.print(f"[bold blue]Rolling back migrations to {revision}...[/bold blue]")

    try:
        from alembic.config import Config
        from alembic import command

        # Get the project root (where alembic.ini lives)
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        alembic_ini = os.path.join(project_root, "alembic.ini")

        if not os.path.exists(alembic_ini):
            console.print(f"[red]✗ alembic.ini not found at {alembic_ini}[/red]")
            raise typer.Exit(1)

        alembic_cfg = Config(alembic_ini)
        command.downgrade(alembic_cfg, revision)

        console.print(f"[green]✓[/green] Rollback completed successfully")
    except ImportError:
        console.print("[red]✗ Alembic not installed. Install with: pip install sqlery[standalone][/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]✗ Rollback failed: {e}[/red]")
        raise typer.Exit(1)


@migrate_app.command("current")
def migrate_current():
    """Show current migration revision."""
    from ..compat import is_django_mode
    import os

    if is_django_mode():
        console.print("[red]Error: Use Django migrations in Django mode[/red]")
        raise typer.Exit(1)

    try:
        from alembic.config import Config
        from alembic import command

        # Get the project root (where alembic.ini lives)
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        alembic_ini = os.path.join(project_root, "alembic.ini")

        if not os.path.exists(alembic_ini):
            console.print(f"[red]✗ alembic.ini not found at {alembic_ini}[/red]")
            raise typer.Exit(1)

        alembic_cfg = Config(alembic_ini)
        command.current(alembic_cfg, verbose=True)
    except ImportError:
        console.print("[red]✗ Alembic not installed. Install with: pip install sqlery[standalone][/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]✗ Failed to get current revision: {e}[/red]")
        raise typer.Exit(1)


@migrate_app.command("history")
def migrate_history():
    """Show migration history."""
    from ..compat import is_django_mode
    import os

    if is_django_mode():
        console.print("[red]Error: Use Django migrations in Django mode[/red]")
        raise typer.Exit(1)

    try:
        from alembic.config import Config
        from alembic import command

        # Get the project root (where alembic.ini lives)
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        alembic_ini = os.path.join(project_root, "alembic.ini")

        if not os.path.exists(alembic_ini):
            console.print(f"[red]✗ alembic.ini not found at {alembic_ini}[/red]")
            raise typer.Exit(1)

        alembic_cfg = Config(alembic_ini)
        command.history(alembic_cfg, verbose=True)
    except ImportError:
        console.print("[red]✗ Alembic not installed. Install with: pip install sqlery[standalone][/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]✗ Failed to get migration history: {e}[/red]")
        raise typer.Exit(1)


# ============================================================================
# Initialize Command
# ============================================================================

@app.command("init")
def initialize(
    database_url: str = typer.Option(..., "--database-url", "-d", help="PostgreSQL connection URL"),
    max_workers: int = typer.Option(3, "--max-workers", "-w", help="Maximum worker processes"),
    pool_size: int = typer.Option(5, "--pool-size", help="Connection pool size"),
    max_overflow: int = typer.Option(10, "--max-overflow", help="Max overflow connections"),
    pool_timeout: int = typer.Option(30, "--pool-timeout", help="Connection pool timeout in seconds"),
):
    """Initialize sqlery for standalone mode."""
    from ..compat import is_django_mode, initialize

    if is_django_mode():
        console.print("[red]Error: Initialization not needed in Django mode[/red]")
        raise typer.Exit(1)

    console.print("[bold blue]Initializing sqlery...[/bold blue]")

    try:
        # initialize(
        #     database_url=database_url,
        #     max_workers=max_workers,
        # )
        initialize(
            database_url=database_url,
            max_workers=max_workers,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout,
        )
        console.print("[green]✓[/green] Initialization successful")
    except Exception as e:
        console.print(f"[red]✗ Initialization failed: {e}[/red]")
        raise typer.Exit(1)


# ============================================================================
# Main Entry Point
# ============================================================================

def main():
    """Main entry point for CLI."""
    try:
        import sqlery.fastapi_sqlery.cli  # noqa: F401 - registers worker/web commands on app
    except Exception:
        pass  # standalone deps (jinja2, uvicorn, etc.) may not be installed
    app()


def daemon_main():
    """Entry point for sqlery-daemon."""
    daemon_app()


def jobs_main():
    """Entry point for sqlery-jobs."""
    jobs_app()


def cleanup_main():
    """Entry point for sqlery-cleanup."""
    cleanup_app()


def migrate_main():
    """Entry point for sqlery-migrate."""
    migrate_app()


def tasks_main():
    """Entry point for sqlery-tasks."""
    tasks_app()


def queues_main():
    """Entry point for sqlery-queues."""
    queues_app()


if __name__ == "__main__":
    main()
