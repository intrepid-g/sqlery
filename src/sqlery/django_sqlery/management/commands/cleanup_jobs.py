"""Management command for database cleanup."""

from django.core.management.base import BaseCommand
from .cleanup import CleanupManager


class Command(BaseCommand):
    help = 'Clean up old jobs and registry entries'

    def add_arguments(self, parser):
        parser.add_argument(
            'action',
            type=str,
            choices=['auto', 'jobs', 'registries', 'stats', 'vacuum'],
            help='Cleanup action to perform',
        )
        parser.add_argument(
            '--status',
            type=str,
            help='Job status to filter by (for jobs action)',
        )
        parser.add_argument(
            '--days',
            type=int,
            help='Max age in days',
        )
        parser.add_argument(
            '--count',
            type=int,
            help='Max number of jobs to keep (for count-based cleanup)',
        )
        parser.add_argument(
            '--registry-type',
            type=str,
            help='Registry type to clean up',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting',
        )

    def handle(self, *args, **options):
        action = options['action']
        manager = CleanupManager()

        if action == 'auto':
            self.auto_cleanup(manager, options.get('dry_run', False))
        elif action == 'jobs':
            self.cleanup_jobs(manager, options)
        elif action == 'registries':
            self.cleanup_registries(manager, options)
        elif action == 'stats':
            self.show_stats(manager)
        elif action == 'vacuum':
            self.vacuum_database(manager)

    def auto_cleanup(self, manager, dry_run):
        """Run automatic cleanup based on configuration."""
        self.stdout.write("\n=== Automatic Cleanup ===\n")

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN MODE - No data will be deleted\n"))

        results = manager.auto_cleanup(dry_run=dry_run)

        if not results['actions']:
            self.stdout.write(self.style.SUCCESS("✓ No cleanup needed"))
            return

        for action in results['actions']:
            action_type = action['action']
            result = action['result']

            if action_type == 'cleanup_by_age':
                status = action['status']
                deleted = result.get('deleted', result.get('would_delete', 0))
                if deleted > 0:
                    verb = "Would delete" if dry_run else "Deleted"
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"✓ {verb} {deleted} {status} jobs older than cutoff"
                        )
                    )

            elif action_type == 'cleanup_by_count':
                status = action['status']
                deleted = result.get('deleted', result.get('would_delete', 0))
                if deleted > 0:
                    kept = result.get('kept', result.get('would_keep', 0))
                    verb = "Would delete" if dry_run else "Deleted"
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"✓ {verb} {deleted} {status} jobs (kept {kept} most recent)"
                        )
                    )

            elif action_type == 'cleanup_registries':
                registry_type = action['registry_type']
                deleted = result.get('deleted', result.get('would_delete', 0))
                if deleted > 0:
                    verb = "Would delete" if dry_run else "Deleted"
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"✓ {verb} {deleted} {registry_type} registry entries"
                        )
                    )

        self.stdout.write("")

    def cleanup_jobs(self, manager, options):
        """Clean up jobs by age or count."""
        status = options.get('status')
        days = options.get('days')
        count = options.get('count')
        dry_run = options.get('dry_run', False)

        self.stdout.write("\n=== Job Cleanup ===\n")

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN MODE - No data will be deleted\n"))

        if days is not None:
            # Age-based cleanup
            result = manager.cleanup_old_jobs(
                status=status,
                max_age_days=days,
                dry_run=dry_run
            )

            deleted = result.get('deleted', result.get('would_delete', 0))
            if deleted > 0:
                verb = "Would delete" if dry_run else "Deleted"
                status_str = f"{status} " if status else ""
                self.stdout.write(
                    self.style.SUCCESS(
                        f"✓ {verb} {deleted} {status_str}jobs older than {days} days"
                    )
                )
                self.stdout.write(f"  Cutoff date: {result['cutoff_date']}")
            else:
                self.stdout.write(self.style.SUCCESS("✓ No jobs to clean up"))

        elif count is not None:
            # Count-based cleanup
            result = manager.cleanup_by_count(
                status=status,
                keep_count=count,
                dry_run=dry_run
            )

            deleted = result.get('deleted', result.get('would_delete', 0))
            if deleted > 0:
                verb = "Would delete" if dry_run else "Deleted"
                status_str = f"{status} " if status else ""
                self.stdout.write(
                    self.style.SUCCESS(
                        f"✓ {verb} {deleted} {status_str}jobs (kept {count} most recent)"
                    )
                )
            else:
                message = result.get('message', 'No jobs to clean up')
                self.stdout.write(self.style.SUCCESS(f"✓ {message}"))

        else:
            self.stderr.write(
                self.style.ERROR("Error: Must specify --days or --count")
            )

        self.stdout.write("")

    def cleanup_registries(self, manager, options):
        """Clean up registry entries."""
        registry_type = options.get('registry_type')
        days = options.get('days')
        dry_run = options.get('dry_run', False)

        self.stdout.write("\n=== Registry Cleanup ===\n")

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN MODE - No data will be deleted\n"))

        result = manager.cleanup_old_registries(
            registry_type=registry_type,
            max_age_days=days,
            dry_run=dry_run
        )

        deleted = result.get('deleted', result.get('would_delete', 0))
        if deleted > 0:
            verb = "Would delete" if dry_run else "Deleted"
            type_str = f"{registry_type} " if registry_type else ""
            self.stdout.write(
                self.style.SUCCESS(
                    f"✓ {verb} {deleted} {type_str}registry entries"
                )
            )
            self.stdout.write(f"  Cutoff date: {result['cutoff_date']}")
        else:
            self.stdout.write(self.style.SUCCESS("✓ No registry entries to clean up"))

        self.stdout.write("")

    def show_stats(self, manager):
        """Show database statistics."""
        self.stdout.write("\n=== Database Statistics ===\n")

        stats = manager.get_database_stats()

        # Job counts
        self.stdout.write("Jobs by status:")
        for status, count in stats.get('job_counts', {}).items():
            self.stdout.write(f"  {status}: {count:,}")
        self.stdout.write(f"  Total: {stats.get('total_jobs', 0):,}\n")

        # Registry counts
        self.stdout.write("Registries by type:")
        for registry_type, count in stats.get('registry_counts', {}).items():
            self.stdout.write(f"  {registry_type}: {count:,}")
        self.stdout.write(f"  Total: {stats.get('total_registries', 0):,}\n")

        # Table sizes (PostgreSQL only)
        if 'jobs_table_size' in stats:
            self.stdout.write("Table sizes:")
            self.stdout.write(f"  Jobs: {stats['jobs_table_size']}")
            self.stdout.write(f"  Registries: {stats['registry_table_size']}\n")

        self.stdout.write("")

    def vacuum_database(self, manager):
        """Run database vacuum."""
        self.stdout.write("\n=== Database Vacuum ===\n")

        result = manager.vacuum_database()

        if result['success']:
            self.stdout.write(
                self.style.SUCCESS(f"✓ {result['message']}")
            )
        else:
            self.stderr.write(
                self.style.ERROR(f"✗ {result.get('error', result['message'])}")
            )

        self.stdout.write("")
