"""Tests for the partial pending index on QueuedJob (plan 12-01).

Verifies:
- sqlery_job_pending_idx exists with correct fields and condition
- Old unnamed full-composite index is absent from Meta.indexes
- Migration 0028 exists with atomic=False, AddIndexConcurrently, RemoveIndexConcurrently
"""

import os

import pytest

pytestmark = pytest.mark.django_db


def get_queued_job_indexes():
    """Return the Meta.indexes list from QueuedJob without importing at module level."""
    from sqlery.django_sqlery.models import QueuedJob
    return list(QueuedJob._meta.indexes)


class TestPartialPendingIndex:
    """QueuedJob.Meta.indexes: partial index assertions."""

    def test_partial_index_exists(self):
        """sqlery_job_pending_idx must exist in Meta.indexes."""
        indexes = get_queued_job_indexes()
        names = [getattr(i, "name", "") for i in indexes]
        assert "sqlery_job_pending_idx" in names, (
            f"sqlery_job_pending_idx not found in indexes: {names}"
        )

    def test_partial_index_fields(self):
        """sqlery_job_pending_idx must cover (queue_name, -priority, created_at)."""
        indexes = get_queued_job_indexes()
        idx = next(
            (i for i in indexes if getattr(i, "name", "") == "sqlery_job_pending_idx"),
            None,
        )
        assert idx is not None, "sqlery_job_pending_idx not found"
        assert idx.fields == ["queue_name", "-priority", "created_at"], (
            f"Expected ['queue_name', '-priority', 'created_at'], got {idx.fields}"
        )

    def test_partial_index_condition(self):
        """sqlery_job_pending_idx must have condition=Q(status='queued')."""
        from django.db.models import Q
        indexes = get_queued_job_indexes()
        idx = next(
            (i for i in indexes if getattr(i, "name", "") == "sqlery_job_pending_idx"),
            None,
        )
        assert idx is not None, "sqlery_job_pending_idx not found"
        expected = Q(status="queued")
        assert idx.condition == expected, (
            f"Expected condition Q(status='queued'), got {idx.condition}"
        )

    def test_old_full_composite_index_absent(self):
        """The old unnamed index fields=['queue_name','status','-priority','created_at'] must be absent."""
        indexes = get_queued_job_indexes()
        old_fields = ["queue_name", "status", "-priority", "created_at"]
        for idx in indexes:
            assert idx.fields != old_fields, (
                f"Old full-composite index (unnamed, fields={old_fields}) still present — must be commented out"
            )


class TestMigration0028:
    """Migration 0028 structure assertions."""

    def test_migration_file_exists(self):
        """0028_partial_pending_index.py must exist."""
        import sqlery.django_sqlery.migrations as mig_pkg
        migrations_dir = os.path.dirname(mig_pkg.__file__)
        migration_path = os.path.join(migrations_dir, "0028_partial_pending_index.py")
        assert os.path.exists(migration_path), f"Migration file not found: {migration_path}"

    def test_migration_atomic_false(self):
        """Migration.atomic must be False for concurrent operations."""
        import importlib
        mod = importlib.import_module("sqlery.django_sqlery.migrations.0028_partial_pending_index")
        migration = mod.Migration
        assert migration.atomic is False, f"atomic must be False, got {migration.atomic}"

    def test_migration_has_add_index_concurrently(self):
        """Migration must contain AddIndexConcurrently."""
        import importlib
        from django.contrib.postgres.operations import AddIndexConcurrently
        mod = importlib.import_module("sqlery.django_sqlery.migrations.0028_partial_pending_index")
        migration_instance = mod.Migration("0028_partial_pending_index", "sqlery")
        ops = migration_instance.operations
        has_add = any(isinstance(op, AddIndexConcurrently) for op in ops)
        assert has_add, f"Missing AddIndexConcurrently in operations: {[type(op).__name__ for op in ops]}"

    def test_migration_has_remove_index_concurrently(self):
        """Migration must contain RemoveIndexConcurrently."""
        import importlib
        from django.contrib.postgres.operations import RemoveIndexConcurrently
        mod = importlib.import_module("sqlery.django_sqlery.migrations.0028_partial_pending_index")
        migration_instance = mod.Migration("0028_partial_pending_index", "sqlery")
        ops = migration_instance.operations
        has_remove = any(isinstance(op, RemoveIndexConcurrently) for op in ops)
        assert has_remove, f"Missing RemoveIndexConcurrently in operations: {[type(op).__name__ for op in ops]}"

    def test_migration_chains_from_0027(self):
        """Migration dependencies must chain from 0027."""
        import importlib
        mod = importlib.import_module("sqlery.django_sqlery.migrations.0028_partial_pending_index")
        migration_instance = mod.Migration("0028_partial_pending_index", "sqlery")
        deps = migration_instance.dependencies
        dep_names = [dep[1] for dep in deps if dep[0] == "sqlery"]
        assert any("0027" in d for d in dep_names), (
            f"Expected dependency on 0027_*, got {deps}"
        )
