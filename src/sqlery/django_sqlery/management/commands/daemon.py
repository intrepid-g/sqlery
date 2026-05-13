"""Management command for controlling the daemon worker.

Updated in v0.11.0 to use core.daemon.DaemonManager.
"""

from django.core.management.base import BaseCommand
from sqlery.core.daemon import DaemonManager


class Command(BaseCommand):
    help = 'Control the sqlery daemon worker'

    def add_arguments(self, parser):
        parser.add_argument(
            'action',
            type=str,
            choices=['start', 'status', 'stop', 'restart'],
            help='Action to perform (start, status, stop, restart)',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force stop daemon (SIGKILL instead of SIGTERM)',
        )
        parser.add_argument(
            '--workers', '-w',
            type=int,
            default=None,
            help='Number of worker subprocess processes to maintain (default: 1)',
        )
        parser.add_argument(
            '--once',
            action='store_true',
            default=False,
            help=(
                'Run a single daemon cycle and exit. Intended for testing / '
                'one-shot integration harnesses. Only valid with action=start.'
            ),
        )

    def handle(self, *args, **options):
        action = options['action']

        max_workers = options.get('workers')
        once = options.get('once', False)

        if action == 'start':
            self.start_daemon(max_workers=max_workers, once=once)
        elif action == 'status':
            self.show_status()
        elif action == 'stop':
            self.stop_daemon(force=options.get('force', False))
        elif action == 'restart':
            self.restart_daemon(force=options.get('force', False), max_workers=max_workers)

        if action != 'start' and once:
            self.stdout.write(self.style.WARNING(
                "--once is ignored for actions other than 'start'."
            ))

    def show_status(self):
        """Show daemon status."""
        from sqlery.django_sqlery.settings import get_setting

        daemon = DaemonManager()
        status = daemon.status()

        self.stdout.write("\n=== Sqlery Daemon Status ===\n")

        if status['running']:
            self.stdout.write(
                self.style.SUCCESS(f"✓ Daemon is RUNNING (PID: {status['pid']})")
            )

            if status['heartbeat_age'] is not None:
                if status['stale']:
                    self.stdout.write(
                        self.style.WARNING(
                            f"  ⚠ Heartbeat is stale ({status['heartbeat_age']}s ago)"
                        )
                    )
                else:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  Last heartbeat: {status['heartbeat_age']}s ago"
                        )
                    )
        else:
            if status['pid'] is not None:
                self.stdout.write(
                    self.style.ERROR(
                        f"✗ Daemon is NOT running (stale PID: {status['pid']})"
                    )
                )
                self.stdout.write("  Run with --cleanup to remove stale PID file")
            else:
                self.stdout.write(self.style.WARNING("⚠ Daemon is NOT running"))

        # Show worker pool status
        max_workers = get_setting('MAX_WORKERS_PER_NODE', 1)
        queues = get_setting('WORKER_QUEUES', ['default'])

        self.stdout.write("\n=== Worker Pool Status ===\n")
        try:
            from sqlery.core.worker_pool import WorkerPoolManager

            pool = WorkerPoolManager(max_workers, queues)
            pool_status = pool.get_status()

            self.stdout.write(
                f"Active Workers: {pool_status['active_count']} / {pool_status['max_workers']}"
            )
            self.stdout.write(
                f"  Idle: {pool_status['idle_count']}, "
                f"Busy: {pool_status['busy_count']}, "
                f"Dead: {pool_status['dead_count']}"
            )

            if pool_status['workers']:
                self.stdout.write("\nActive Workers:")
                for worker in pool_status['workers']:
                    worker_id_short = str(worker['id'])[:8]
                    status_color = (
                        self.style.SUCCESS if worker['status'] == 'idle'
                        else self.style.WARNING
                    )
                    self.stdout.write(
                        f"  - Worker {worker_id_short} [PID {worker['pid']}] "
                        f"{status_color(worker['status'])} - "
                        f"Processed: {worker['jobs_processed']}"
                    )
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  Failed to get worker status: {e}"))

        self.stdout.write("")

    def start_daemon(self, max_workers: int | None = None, once: bool = False):
        """Start the daemon in the foreground.

        Args:
            max_workers: Override worker pool size.
            once: If True, run a single daemon cycle and return (test harness).
        """
        daemon = DaemonManager()
        if daemon.is_running():
            self.stdout.write(self.style.WARNING("Daemon is already running"))
            return
        self.stdout.write(
            f"Starting daemon (workers={max_workers or 'default'}, once={once})..."
        )
        daemon._run_daemon(max_workers=max_workers, once=once)

    def stop_daemon(self, force=False):
        """Stop the daemon."""
        self.stdout.write("Stopping daemon...")

        daemon = DaemonManager()
        if daemon.stop(force=force):
            self.stdout.write(self.style.SUCCESS("✓ Daemon stopped"))
        else:
            self.stdout.write(self.style.WARNING("⚠ Daemon was not running"))

    def restart_daemon(self, force=False, max_workers: int | None = None):
        """Restart the daemon."""
        self.stdout.write("Restarting daemon...")

        try:
            daemon = DaemonManager()

            # Stop if running
            if daemon.is_running():
                self.stdout.write("  Stopping existing daemon...")
                daemon.stop(force=force)

            # Cleanup stale files
            daemon.cleanup_stale()

            # Spawn new daemon
            self.stdout.write("  Starting daemon...")

            # Use core daemon_runner (don't specify script, let it use default)
            process = daemon.spawn_daemon()
            if process:
                self.stdout.write(self.style.SUCCESS(f"✓ Daemon restarted (PID: {process.pid})"))
            else:
                self.stdout.write(self.style.WARNING("⚠ Daemon was already running"))

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f"✗ Failed to restart daemon: {e}")
            )
