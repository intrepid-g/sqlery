"""Management command to run scheduled tasks."""

from django.core.management.base import BaseCommand
from sqlery.core.worker import TaskExecutor
from sqlery.django_sqlery.models import ScheduledTask


class Command(BaseCommand):
    help = "Run all due scheduled tasks"

    def add_arguments(self, parser):
        parser.add_argument(
            "--task",
            type=str,
            help="Run specific task by name",
        )

    def handle(self, *args, **options):
        executor = TaskExecutor()

        if options["task"]:
            # Run specific task
            try:
                task = ScheduledTask.objects.get(name=options["task"], enabled=True)
                execution = executor.execute_task(task)
                if execution:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Executed '{task.name}': {execution.status}"
                        )
                    )
                else:
                    self.stdout.write(
                        self.style.WARNING(f"Task '{task.name}' already running")
                    )
            except ScheduledTask.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(
                        f"Task '{options['task']}' not found or disabled"
                    )
                )
        else:
            # Run all due tasks
            executions = executor.run_due_tasks()
            self.stdout.write(self.style.SUCCESS(f"Executed {len(executions)} tasks"))
