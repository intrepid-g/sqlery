"""Management command for worker control.

Updated in v0.11.0 to use core.worker_pool.WorkerPoolManager.
"""

from django.core.management.base import BaseCommand
from sqlery.django_sqlery.models import Worker
from sqlery.core.worker_pool import WorkerPoolManager
from sqlery.django_sqlery.settings import get_setting


class Command(BaseCommand):
    help = 'Control sqlery worker processes'

    def add_arguments(self, parser):
        parser.add_argument(
            'action',
            type=str,
            choices=['list', 'kill', 'cleanup', 'stop-all'],
            help='Action to perform',
        )
        parser.add_argument(
            'worker_id',
            type=str,
            nargs='?',
            help='Worker ID (for kill action)',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force kill with SIGKILL',
        )

    def handle(self, *args, **options):
        action = options['action']

        if action == 'list':
            self.list_workers()
        elif action == 'kill':
            if not options['worker_id']:
                self.stderr.write(self.style.ERROR("Error: worker_id is required for kill action"))
                return
            self.kill_worker(options['worker_id'], force=options.get('force', False))
        elif action == 'cleanup':
            self.cleanup_dead_workers()
        elif action == 'stop-all':
            self.stop_all_workers(force=options.get('force', False))

    def list_workers(self):
        """List all workers."""
        max_workers = get_setting('MAX_WORKERS_PER_NODE', 1)
        queues = get_setting('WORKER_QUEUES', ['default'])

        if max_workers == 1:
            self.stdout.write(
                self.style.WARNING("⚠ Single-worker mode (MAX_WORKERS_PER_NODE=1)")
            )
            self.stdout.write("  No worker pool active.\n")
            return

        self.stdout.write("\n=== Sqlery Workers ===\n")

        try:
            pool = WorkerPoolManager(max_workers, queues)
            pool_status = pool.get_status()

            self.stdout.write(
                f"Active Workers: {pool_status['active_count']} / {pool_status['max_workers']}\n"
            )
            self.stdout.write(
                f"  Idle: {self.style.SUCCESS(str(pool_status['idle_count']))}, "
                f"Busy: {self.style.WARNING(str(pool_status['busy_count']))}, "
                f"Dead: {self.style.ERROR(str(pool_status['dead_count']))}\n"
            )

            if not pool_status['workers']:
                self.stdout.write("\nNo active workers\n")
                return

            self.stdout.write("\nActive Workers:\n")
            self.stdout.write("-" * 100)

            for worker in pool_status['workers']:
                worker_id = str(worker['id'])
                worker_id_short = worker_id[:8]

                status_style = (
                    self.style.SUCCESS if worker['status'] == 'idle'
                    else self.style.WARNING
                )

                current_job = (
                    f"Job #{worker['current_job_id']}"
                    if worker['current_job_id']
                    else "None"
                )

                self.stdout.write(
                    f"\n  Worker ID:     {worker_id_short}... (full: {worker_id})"
                )
                self.stdout.write(
                    f"  Node:          {worker['node_id']}"
                )
                self.stdout.write(
                    f"  PID:           {worker['pid']}"
                )
                self.stdout.write(
                    f"  Status:        {status_style(worker['status'])}"
                )
                self.stdout.write(
                    f"  Current Job:   {current_job}"
                )
                self.stdout.write(
                    f"  Jobs Done:     {worker['jobs_processed']}"
                )
                self.stdout.write(
                    f"  Started:       {worker['started_at']}"
                )
                self.stdout.write(
                    f"  Last Heartbeat: {worker['last_heartbeat']}"
                )
                self.stdout.write("-" * 100)

            self.stdout.write("")

        except Exception as e:
            self.stderr.write(
                self.style.ERROR(f"Failed to list workers: {e}")
            )

    def kill_worker(self, worker_id, force=False):
        """Kill a specific worker."""
        import os
        import signal

        self.stdout.write(f"Killing worker {worker_id[:8]}...")

        try:
            # Get worker from database
            worker = Worker.objects.filter(id=worker_id).first()
            if not worker:
                self.stdout.write(
                    self.style.WARNING("⚠ Worker not found")
                )
                return

            # Send signal to process
            sig = signal.SIGKILL if force else signal.SIGTERM
            sig_name = "SIGKILL" if force else "SIGTERM"

            try:
                os.kill(worker.pid, sig)
                self.stdout.write(
                    self.style.SUCCESS(f"✓ Worker killed ({sig_name})")
                )

                # Mark as dead in database
                from sqlery.compat import get_backend
                backend = get_backend()
                backend.update_worker_heartbeat(
                    worker_id=str(worker.id),
                    status='dead',
                    current_job_id=None
                )

            except ProcessLookupError:
                self.stdout.write(
                    self.style.WARNING("⚠ Worker process already dead")
                )
            except PermissionError:
                self.stdout.write(
                    self.style.ERROR("✗ Permission denied")
                )

        except Exception as e:
            self.stderr.write(
                self.style.ERROR(f"✗ Failed to kill worker: {e}")
            )

    def cleanup_dead_workers(self):
        """Clean up dead workers."""
        self.stdout.write("Cleaning up dead workers...")

        try:
            max_workers = get_setting('MAX_WORKERS_PER_NODE', 1)
            queues = get_setting('WORKER_QUEUES', ['default'])

            pool = WorkerPoolManager(max_workers, queues)
            count = pool.cleanup_dead_workers()

            if count > 0:
                self.stdout.write(
                    self.style.SUCCESS(f"✓ Cleaned up {count} dead workers")
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS("✓ No dead workers found")
                )
        except Exception as e:
            self.stderr.write(
                self.style.ERROR(f"✗ Failed to cleanup workers: {e}")
            )

    def stop_all_workers(self, force=False):
        """Stop all workers."""
        self.stdout.write("Stopping all workers...")

        try:
            max_workers = get_setting('MAX_WORKERS_PER_NODE', 1)
            queues = get_setting('WORKER_QUEUES', ['default'])

            pool = WorkerPoolManager(max_workers, queues)
            count = pool.stop_all_workers(force=force)

            if count > 0:
                sig = "SIGKILL" if force else "SIGTERM"
                self.stdout.write(
                    self.style.SUCCESS(f"✓ Stopped {count} workers ({sig})")
                )
            else:
                self.stdout.write(
                    self.style.WARNING("⚠ No workers were running")
                )
        except Exception as e:
            self.stderr.write(
                self.style.ERROR(f"✗ Failed to stop workers: {e}")
            )
