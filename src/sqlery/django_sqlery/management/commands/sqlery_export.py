"""Export scheduled tasks to JSON or YAML for backup and migration."""

import json
import sys
from io import StringIO

from django.core.management import call_command
from django.core.management.base import BaseCommand

from sqlery.django_sqlery.models import ScheduledTask


class Command(BaseCommand):
    help = "Export scheduled tasks to JSON or YAML format"

    def add_arguments(self, parser):
        parser.add_argument(
            "-o", "--output",
            choices=["json", "yaml"], default="json",
            help="Output format (default: json)",
        )
        parser.add_argument(
            "-e", "--enabled",
            action="store_true",
            help="Export only enabled tasks",
        )
        parser.add_argument(
            "-f", "--filename",
            help="Output file path (default: stdout)",
        )
        parser.add_argument(
            "--django-fixture",
            action="store_true",
            help="Export as Django fixture with natural keys (smuggler-compatible)",
        )

    def handle(self, *args, **options):
        # Django fixture mode: delegate to dumpdata with natural keys
        if options["django_fixture"]:
            buf = StringIO()
            call_command(
                "dumpdata",
                "sqlery.scheduledtask",
                stdout=buf,
                indent=2,
                use_natural_foreign_keys=True,
                use_natural_primary_keys=True,
            )
            content = buf.getvalue()

            if options["filename"]:
                with open(options["filename"], "w") as f:
                    f.write(content)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Exported Django fixture to {options['filename']}"
                    )
                )
            else:
                self.stdout.write(content)
            return

        # Flat format (original behavior)
        tasks = ScheduledTask.objects.all().order_by("name")
        if options["enabled"]:
            tasks = tasks.filter(enabled=True)

        data = []
        for task in tasks:
            data.append({
                "name": task.name,
                "task_path": task.task_path,
                "task_kwargs": task.task_kwargs,
                "schedule_type": task.schedule_type,
                "cron_expression": task.cron_expression,
                "interval": task.interval,
                "interval_unit": task.interval_unit,
                "repeat": task.repeat,
                "scheduled_time": (
                    task.scheduled_time.isoformat() if task.scheduled_time else None
                ),
                "queue_name": task.queue_name,
                "priority": task.priority,
                "enabled": task.enabled,
            })

        output_format = options["output"]
        if output_format == "yaml":
            try:
                import yaml
                content = yaml.dump(data, default_flow_style=False)
            except ImportError:
                self.stderr.write(
                    self.style.ERROR("PyYAML required for YAML: pip install pyyaml")
                )
                return
        else:
            from django.core.serializers.json import DjangoJSONEncoder
            content = json.dumps(data, indent=2, cls=DjangoJSONEncoder)

        if options["filename"]:
            with open(options["filename"], "w") as f:
                f.write(content)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Exported {len(data)} tasks to {options['filename']}"
                )
            )
        else:
            self.stdout.write(content)
