"""CLI entry points for standalone mode.

This module provides CLI entry points for standalone mode, wrapping the
core CLI with additional standalone-specific commands.
"""

import typer
from rich.console import Console

# Import the core CLI app
from ..core.cli import app

console = Console()


# ============================================================================
# Standalone-Specific Commands
# ============================================================================

@app.command("worker")
def worker_command(
    queues: str = typer.Option("default", "--queues", "-q", help="Comma-separated queue names"),
    max_jobs: int = typer.Option(0, "--max-jobs", "-m", help="Max jobs to process (0=unlimited)"),
):
    """Run a standalone worker process."""
    from ..compat import is_django_mode
    from ..core.worker import Worker

    if is_django_mode():
        console.print("[red]Error: Use 'python manage.py worker' in Django mode[/red]")
        raise typer.Exit(1)

    queue_list = [q.strip() for q in queues.split(",")]

    console.print(f"[bold blue]Starting worker for queues: {', '.join(queue_list)}[/bold blue]")

    try:
        worker = Worker(queues=queue_list)
        worker.run(max_jobs=max_jobs)
    except KeyboardInterrupt:
        console.print("\n[yellow]Worker stopped by user[/yellow]")
    except Exception as e:
        console.print(f"[red]✗ Worker error: {e}[/red]")
        raise typer.Exit(1)


@app.command("web")
def web_command(
    host: str = typer.Option("0.0.0.0", "--host", "-h", help="Host to bind to"),
    port: int = typer.Option(8000, "--port", "-p", help="Port to bind to"),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload for development"),
):
    """Run the web UI server."""
    from ..compat import is_django_mode

    if is_django_mode():
        console.print("[red]Error: Use 'python manage.py runserver' in Django mode[/red]")
        raise typer.Exit(1)

    console.print(f"[bold blue]Starting web UI on {host}:{port}[/bold blue]")

    try:
        import uvicorn
        from .app import app as fastapi_app

        uvicorn.run(
            fastapi_app,
            host=host,
            port=port,
            reload=reload,
            log_level="info",
        )
    except ImportError:
        console.print("[red]✗ FastAPI/Uvicorn not installed. Install with: pip install sqlery[standalone][/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]✗ Web server error: {e}[/red]")
        raise typer.Exit(1)


# ============================================================================
# Standalone Entry Points
# ============================================================================

def worker_main():
    """Entry point for sqlery-worker."""
    _worker_app = typer.Typer(no_args_is_help=True)
    _worker_app.command("run")(worker_command)
    _worker_app()


def web_main():
    """Entry point for sqlery-web."""
    _web_app = typer.Typer(no_args_is_help=True)
    _web_app.command("run")(web_command)
    _web_app()


if __name__ == "__main__":
    app()
