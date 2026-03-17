"""Management command to run both scheduler and queue workers.

NOTE: Each invocation processes EXACTLY ONE job (memory leak prevention).
For multiple jobs, invoke this command multiple times or use middleware
to spawn multiple worker subprocesses.
"""

from django.core.management.base import BaseCommand
from sqlery.django_sqlery.executor import TaskExecutor


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
            processed = executor.run_queue_workers(
                queue_name=options.get("queue")
            )
            self.stdout.write(
                self.style.SUCCESS(f"Worker: Processed {len(processed)} jobs")
            )
