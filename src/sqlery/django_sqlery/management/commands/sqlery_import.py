"""Import scheduled tasks from JSON or YAML backup."""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone as dt_timezone
from io import StringIO

try:
    import yaml
except ImportError:
    yaml = None

from django.core.management import call_command
from django.core.management.base import BaseCommand

from sqlery.django_sqlery.models import ScheduledTask


def _is_django_fixture(data: list) -> bool:
    """Detect Django fixture format (list of dicts with 'model' + 'fields' keys)."""
    if not data:
        return False
    first = data[0]
    return isinstance(first, dict) and "model" in first and "fields" in first


class Command(BaseCommand):
    help = "Import scheduled tasks from JSON or YAML format"

    def add_arguments(self, parser):
        parser.add_argument(
            "-f", "--format",
            choices=["json", "yaml"], default="json",
            help="Input format (default: json)",
        )
        parser.add_argument(
            "--filename",
            help="Input file path (default: stdin)",
        )
        parser.add_argument(
            "-r", "--reset",
            action="store_true",
            help="Delete all existing tasks before import",
        )
        parser.add_argument(
            "-u", "--update",
            action="store_true",
            help="Update existing tasks by name (default: skip existing)",
        )

    def handle(self, *args, **options):
        # Read input
        if options["filename"]:
            with open(options["filename"]) as f:
                raw = f.read()
        else:
            raw = sys.stdin.read()

        # Parse input
        input_format = options["format"]
        if input_format == "yaml":
            # import yaml  # moved to top-level (optional try/except)
            if yaml is None:
                self.stderr.write(
                    self.style.ERROR("PyYAML required for YAML: pip install pyyaml")
                )
                return
            data = yaml.safe_load(raw)
        else:
            data = json.loads(raw)

        if not isinstance(data, list):
            self.stderr.write(self.style.ERROR("Expected a JSON array of tasks"))
            return

        # Auto-detect Django fixture format and delegate to loaddata
        if _is_django_fixture(data):
            self.stdout.write("Detected Django fixture format, delegating to loaddata...")
            tmp = None
            try:
                tmp = tempfile.NamedTemporaryFile(
                    suffix=".json", delete=False, mode="w",
                )
                tmp.write(raw)
                tmp.close()

                buf = StringIO()
                call_command("loaddata", tmp.name, stdout=buf, verbosity=1)
                self.stdout.write(self.style.SUCCESS(buf.getvalue().strip()))
            finally:
                if tmp:
                    try:
                        os.unlink(tmp.name)
                    except OSError:
                        pass
            return

        # Flat format (original behavior)

        # Reset if requested
        if options["reset"]:
            count = ScheduledTask.objects.count()
            ScheduledTask.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"Deleted {count} existing tasks"))

        created = 0
        updated = 0
        skipped = 0

        for task_data in data:
            name = task_data.get("name")
            if not name:
                self.stderr.write(self.style.WARNING("Skipping task without name"))
                skipped += 1
                continue

            # Parse scheduled_time if present
            scheduled_time = task_data.get("scheduled_time")
            if scheduled_time and isinstance(scheduled_time, str):
                scheduled_time = datetime.fromisoformat(scheduled_time)
                if scheduled_time.tzinfo is None:
                    scheduled_time = scheduled_time.replace(tzinfo=dt_timezone.utc)

            defaults = {
                "task_path": task_data.get("task_path", ""),
                "task_kwargs": task_data.get("task_kwargs", {}),
                "schedule_type": task_data.get("schedule_type", "cron"),
                "cron_expression": task_data.get("cron_expression"),
                "interval": task_data.get("interval"),
                "interval_unit": task_data.get("interval_unit"),
                "repeat": task_data.get("repeat"),
                "scheduled_time": scheduled_time,
                "queue_name": task_data.get("queue_name", "default"),
                "priority": task_data.get("priority", 0),
                "enabled": task_data.get("enabled", True),
            }

            existing = ScheduledTask.objects.filter(name=name).first()

            if existing:
                if options["update"]:
                    for key, value in defaults.items():
                        setattr(existing, key, value)
                    existing.save()
                    updated += 1
                else:
                    skipped += 1
            else:
                ScheduledTask.objects.create(name=name, **defaults)
                created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Import complete: {created} created, {updated} updated, {skipped} skipped"
            )
        )
