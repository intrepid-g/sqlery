"""Compatibility ``rqworker`` management command.

Drop-in replacement for ``django-tasks-scheduler``'s
``python manage.py rqworker`` so projects migrating to SQLery can keep
the same process-management commands without changes.

Usage (identical to django-tasks-scheduler)::

    python manage.py rqworker priority default sync_deapi sync_rapi lowest

Internally delegates to SQLery's daemon loop in foreground mode,
processing jobs from the requested queues.

Deprecated since v3.1.0 — will be removed in v3.2.0.
Use ``python manage.py daemon`` or the SQLery worker API directly.
"""

import warnings

from django.conf import settings
from django.core.management.base import BaseCommand

from sqlery.core.daemon import DaemonManager
from sqlery.django_sqlery.executor import TaskExecutor
from sqlery.django_sqlery.settings import get_setting


class Command(BaseCommand):
    help = (
        "[django-tasks-scheduler compat] Start a worker that processes jobs "
        "from the given queues. Drop-in for django-tasks-scheduler's rqworker."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "queues",
            nargs="*",
            type=str,
            help="Queue names to listen on (default: WORKER_QUEUES setting)",
        )
        parser.add_argument(
            "--burst",
            action="store_true",
            help="Process all pending jobs then exit (like rqworker --burst)",
        )
        parser.add_argument(
            "--workers", "-w",
            type=int,
            default=None,
            help="Number of worker subprocess processes to maintain (default: 1)",
        )

    def handle(self, *args, **options):
        warnings.warn(
            "The 'rqworker' management command is a django-tasks-scheduler "
            "compatibility shim and will be removed in v3.2.0. "
            "Use 'python manage.py daemon' instead.",
            DeprecationWarning,
            stacklevel=2,
        )

        queues = options["queues"] or get_setting("WORKER_QUEUES", ["default"])
        burst = options.get("burst", False)

        self.stdout.write(
            self.style.WARNING(
                "rqworker is a django-tasks-scheduler compatibility shim — "
                "consider switching to 'python manage.py daemon'."
            )
        )
        self.stdout.write(f"Listening on queues: {', '.join(queues)}")

        max_workers = options.get("workers")

        if burst:
            self._run_burst(queues)
        else:
            self._run_daemon(queues, max_workers=max_workers)

    # ------------------------------------------------------------------

    def _run_daemon(self, queues: list[str], max_workers: int | None = None):
        """Start the daemon loop in the foreground (like rqworker)."""
        # Override WORKER_QUEUES so the daemon picks up the CLI-specified queues
        # from django.conf import settings  # moved to top-level

        user_settings = getattr(settings, "DJANGO_SQL_JOBS", {})
        # original_queues = user_settings.get("WORKER_QUEUES")
        user_settings["WORKER_QUEUES"] = list(queues)
        settings.DJANGO_SQL_JOBS = user_settings

        daemon = DaemonManager()
        # Run in foreground — blocks until SIGTERM / SIGINT
        daemon._run_daemon(max_workers=max_workers)

    def _run_burst(self, queues: list[str]):
        """Process all pending jobs from the given queues, then exit."""
        # from sqlery.django_sqlery.executor import TaskExecutor  # moved to top-level

        executor = TaskExecutor()
        total = 0

        for queue_name in queues:
            processed = executor.run_queue_workers(queue_name=queue_name)
            count = len(processed) if processed else 0
            total += count
            if count:
                self.stdout.write(f"  {queue_name}: processed {count} jobs")

        self.stdout.write(
            self.style.SUCCESS(f"Burst complete — processed {total} jobs total.")
        )
