"""Management command to run both scheduler and queue workers.

NOTE: By default each invocation processes EXACTLY ONE job (memory leak
prevention). Pass --max-jobs N to process up to N jobs in one process, and
--idle-linger S to wait up to S seconds for new jobs once the queue is empty
(only while below N). The subprocess trigger passes these through from the
SUBPROCESS_JOBS_PER_PROCESS / SUBPROCESS_IDLE_LINGER_S settings.
"""

from django.core.management.base import BaseCommand
from sqlery.executor import TaskExecutor


class Command(BaseCommand):
    help = "Run scheduler (enqueue due tasks) and/or process ONE job from queue"

    def add_arguments(self, parser):
        parser.add_argument(
            "--queue",
            type=str,
            help="Process specific queue only",
        )
        parser.add_argument(
            "--scheduler-only",
            action="store_true",
            help="Only enqueue due tasks, don't process queue",
        )
        parser.add_argument(
            "--worker-only",
            action="store_true",
            help="Only process queue, don't check scheduled tasks",
        )

        parser.add_argument(
            "--max-jobs",
            type=int,
            default=1,
            help="Jobs to process in this process before exiting (default: 1)",
        )
        parser.add_argument(
            "--idle-linger",
            type=float,
            default=0,
            help="Seconds to wait for new jobs when the queue is empty and fewer than "
            "--max-jobs have run (default: 0 = exit immediately)",
        )

    def handle(self, *args, **options):
        executor = TaskExecutor()

        # Run scheduler (unless --worker-only)
        if not options["worker_only"]:
            jobs = executor.run_due_tasks()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Scheduler: Enqueued {len(jobs)} jobs from scheduled tasks"
                )
            )

        # Run workers (unless --scheduler-only)
        # NOTE: Processes exactly ONE job per invocation
        if not options["scheduler_only"]:
            max_jobs = options["max_jobs"]
            kwargs = {}
            if max_jobs > 1:
                kwargs["max_jobs"] = max_jobs
            if options["idle_linger"] > 0:
                kwargs["idle_linger"] = options["idle_linger"]
            processed = executor.run_queue_workers(
                queue_name=options.get("queue"), **kwargs
            )
            self.stdout.write(
                self.style.SUCCESS(f"Worker: Processed {len(processed)} jobs")
            )
