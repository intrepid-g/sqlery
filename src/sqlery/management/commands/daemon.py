"""Management command for controlling the daemon worker."""

from django.core.management.base import BaseCommand

from sqlery.daemon_manager import (
    get_daemon_status,
    stop_daemon,
    cleanup_stale_pid,
)
from sqlery.daemon_middleware import DaemonMiddleware
from sqlery.settings import get_setting
from sqlery.worker_pool import get_worker_pool_status


class Command(BaseCommand):
    help = 'Control the sqlery daemon worker'

    def add_arguments(self, parser):
        parser.add_argument(
            'action',
            type=str,
            choices=['status', 'stop', 'restart'],
            help='Action to perform (status, stop, restart)',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force stop daemon (SIGKILL instead of SIGTERM)',
        )

    def handle(self, *args, **options):
        action = options['action']

        if action == 'status':
            self.show_status()
        elif action == 'stop':
            self.stop_daemon(force=options.get('force', False))
        elif action == 'restart':
            self.restart_daemon(force=options.get('force', False))

    def show_status(self):
        """Show daemon status."""
        # from sqlery.worker_pool import get_worker_pool_status  # moved to top-level
        # from sqlery.settings import get_setting  # moved to top-level

        status = get_daemon_status()

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

        # Show worker pool status if multi-worker mode
        max_workers = get_setting('MAX_WORKERS_PER_NODE', 1)
        if max_workers > 1:
            self.stdout.write("\n=== Worker Pool Status ===\n")
            try:
                pool_status = get_worker_pool_status()
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

    def stop_daemon(self, force=False):
        """Stop the daemon."""
        self.stdout.write("Stopping daemon...")

        if stop_daemon(force=force):
            self.stdout.write(self.style.SUCCESS("✓ Daemon stopped"))
        else:
            self.stdout.write(self.style.WARNING("⚠ Daemon was not running"))

    def restart_daemon(self, force=False):
        """Restart the daemon."""
        # Stop first
        self.stdout.write("Stopping daemon...")
        stop_daemon(force=force)

        # Cleanup
        cleanup_stale_pid()

        # Start via importing and spawning
        self.stdout.write("Starting daemon...")

        try:
            # from sqlery.daemon_middleware import DaemonMiddleware  # moved to top-level

            middleware = DaemonMiddleware(lambda r: r)
            middleware.spawn_daemon()

            self.stdout.write(self.style.SUCCESS("✓ Daemon restarted"))
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f"✗ Failed to start daemon: {e}")
            )
