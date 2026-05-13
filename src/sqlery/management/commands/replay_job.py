"""Inspect or replay a sqlery job for debugging.

Usage:
    manage.py replay_job 28134              # inspect job details
    manage.py replay_job 28134 --execute    # re-run task in this process (no fork)
"""

import traceback as tb

from django.core.management.base import BaseCommand, CommandError

from sqlery.core.utils import import_task
from sqlery.django_sqlery.models import QueuedJob


class Command(BaseCommand):
    help = "Inspect a job's full details or replay it locally to reproduce errors"

    def add_arguments(self, parser):
        parser.add_argument("job_id", type=int, help="Job ID to inspect or replay")
        parser.add_argument(
            "--execute",
            action="store_true",
            help="Re-run the task function directly in this process (diagnostic only, does not update job status)",
        )

    def handle(self, *args, **options):
        # from sqlery.django_sqlery.models import QueuedJob  # moved to top-level

        job_id = options["job_id"]

        try:
            job = QueuedJob.objects.select_related("worker", "scheduled_task").get(pk=job_id)
        except QueuedJob.DoesNotExist:
            raise CommandError(f"Job #{job_id} not found")

        self._print_job(job)

        if options["execute"]:
            self._replay(job)

    def _print_job(self, job):
        w = self.stdout.write
        s = self.style

        w(s.MIGRATE_HEADING(f"\n=== Job #{job.id} ===\n"))

        status_style = {
            "success": s.SUCCESS,
            "failed": s.ERROR,
            "running": s.WARNING,
            "queued": s.NOTICE,
        }.get(job.status, s.WARNING)

        w(f"  Status:      {status_style(job.status)}")
        w(f"  Task:        {job.task_path}")
        w(f"  Job Name:    {job.job_name or '(none)'}")
        w(f"  Queue:       {job.queue_name}")
        w(f"  Priority:    {job.priority}")

        # Kwargs
        kwargs = job.kwargs if isinstance(job.kwargs, dict) else {}
        positional_args = kwargs.get("_args", [])
        other_kwargs = {k: v for k, v in kwargs.items() if k != "_args"}
        if positional_args:
            w(f"  Args:        {positional_args}")
        if other_kwargs:
            w(f"  Kwargs:      {other_kwargs}")

        # Retry info
        if job.max_retries:
            w(f"  Retry:       {job.retry_count}/{job.max_retries} (backoff={job.retry_backoff}s)")
        if job.parent_job_id:
            w(f"  Parent Job:  #{job.parent_job_id}")

        # Worker info
        w("")
        if job.worker:
            w(f"  Worker:      {job.worker.friendly_name} (PID {job.worker_pid})")
        elif job.worker_pid:
            w(f"  Worker PID:  {job.worker_pid}")
        if job.child_pid:
            w(f"  Child PID:   {job.child_pid}")

        # Timing
        w("")
        w(f"  Created:     {job.created_at}")
        if job.started_at:
            w(f"  Started:     {job.started_at}")
        if job.finished_at:
            w(f"  Finished:    {job.finished_at}")
        if job.duration_seconds is not None:
            w(f"  Duration:    {job.duration_seconds:.3f}s")

        # Results
        if job.error:
            w(f"\n  {s.ERROR('Error:')}")
            for line in job.error.splitlines():
                w(f"    {line}")
        if job.traceback:
            w(f"\n  {s.ERROR('Traceback:')}")
            for line in job.traceback.splitlines():
                w(f"    {line}")
        if job.termination_reason:
            w(f"  Termination: {job.termination_reason}")
        if job.output:
            w(f"\n  {s.SUCCESS('Output:')}")
            for line in str(job.output).splitlines()[:20]:
                w(f"    {line}")

        # Runs history
        if job.runs:
            w(f"\n  {s.MIGRATE_HEADING('Runs history:')}")
            for i, run in enumerate(job.runs):
                status = run.get("status", "?")
                run_style = s.SUCCESS if status == "success" else s.ERROR
                w(f"    [{i}] {run_style(status)} at {run.get('at', '?')}")
                if run.get("error"):
                    w(f"        error: {run['error'][:200]}")

        w("")

    def _replay(self, job):
        w = self.stdout.write
        s = self.style

        w(s.WARNING("\n--- Replaying task (diagnostic mode, job status will NOT be updated) ---\n"))

        # Import task
        # from sqlery.core.utils import import_task  # moved to top-level

        try:
            task_func = import_task(job.task_path)
        except ImportError as e:
            w(s.ERROR(f"Cannot import task: {e}"))
            return

        # Prepare args/kwargs
        kwargs = job.kwargs.copy() if isinstance(job.kwargs, dict) else {}
        positional_args = kwargs.pop("_args", ())

        w(f"  Calling: {job.task_path}(*{list(positional_args)}, **{kwargs})\n")

        # Execute directly — errors print to terminal
        try:
            result = task_func(*positional_args, **kwargs)
            w(s.SUCCESS(f"\nTask returned successfully: {str(result)[:500]}"))
        except Exception as e:
            w(s.ERROR(f"\nTask raised {type(e).__name__}: {e}\n"))
            w(s.ERROR("Full traceback:"))
            w(tb.format_exc())
