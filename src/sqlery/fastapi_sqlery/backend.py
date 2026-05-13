"""SQLAlchemy backend implementation for sqlery standalone mode.

This backend uses SQLModel/SQLAlchemy for all database operations.
"""

import os
import socket
from datetime import datetime, timedelta, timezone as dt_timezone, UTC
from typing import Any

from sqlalchemy import and_, or_, text
from sqlmodel import Session, select, func, delete

from ..compat import DatabaseBackend
from ..core.models import QueuedJob, ScheduledTask, JobRegistry, Worker
from .database import get_session


class SQLAlchemyBackend(DatabaseBackend):
    """SQLAlchemy implementation of DatabaseBackend.

    Uses SQLModel (which wraps SQLAlchemy) for database operations.
    """

    def __init__(self):
        """Initialize SQLAlchemy backend."""
        # from .database import get_session  # moved to top-level

        self._get_session = get_session

    # def create_job(  # Original 9-param signature
    #     self,
    #     task_path: str,
    #     kwargs: dict,
    #     queue_name: str,
    #     priority: int,
    #     scheduled_at: datetime | None,
    #     max_retries: int,
    #     retry_backoff: float,
    #     allow_parallel: bool,
    #     timeout_seconds: int | None,
    #     parent_job_id: int | None = None,
    # ):
    def create_job(
        self,
        task_path: str,
        kwargs: dict,
        queue_name: str,
        priority: int,
        scheduled_at: datetime | None,
        max_retries: int,
        retry_backoff: float,
        allow_parallel: bool,
        timeout_seconds: int | None,
        retry_count: int | None = None,
        scheduled_task_id: int | None = None,
        job_name: str | None = None,
        retry_intervals: list | None = None,
        meta: dict | None = None,
        dependencies: list | None = None,
        on_success_path: str = '',
        on_failure_path: str = '',
        ttl: int | None = None,
        result_ttl: int | None = None,
        failure_ttl: int | None = None,
        parent_job_id: int | None = None,
    ):
        """Create a new job in the database."""
        # Named job support: new job always wins, stop conflicting jobs
        if job_name:
            with self._get_session() as session:
                stmt = select(QueuedJob).where(QueuedJob.job_name == job_name)
                for conflicting in session.exec(stmt).all():
                    session.delete(conflicting)
                session.commit()

        job = QueuedJob(
            task_path=task_path,
            kwargs=kwargs,
            queue_name=queue_name,
            priority=priority,
            scheduled_at=scheduled_at,
            max_retries=max_retries,
            retry_backoff=retry_backoff,
            allow_parallel=allow_parallel,
            timeout_seconds=timeout_seconds,
            retry_count=retry_count if retry_count is not None else 0,
            scheduled_task_id=scheduled_task_id,
            job_name=job_name,
            retry_intervals=retry_intervals,
            meta=meta,
            dependencies=dependencies or [],
            on_success_path=on_success_path,
            on_failure_path=on_failure_path,
            ttl=ttl,
            result_ttl=result_ttl,
            failure_ttl=failure_ttl,
            parent_job_id=parent_job_id,
            status="queued",
        )

        with self._get_session() as session:
            session.add(job)
            session.commit()
            session.refresh(job)

        return job

    def claim_job(self, queues: list[str], worker_id: str):
        """Atomically claim next available job using SELECT FOR UPDATE SKIP LOCKED."""
        with self._get_session() as session:
            # Build query for claimable jobs
            now = datetime.now(UTC)

            stmt = (
                select(QueuedJob)
                .where(
                    and_(
                        QueuedJob.queue_name.in_(queues),
                        QueuedJob.status == "queued",
                        or_(
                            QueuedJob.scheduled_at == None,
                            QueuedJob.scheduled_at <= now
                        )
                    )
                )
                .order_by(QueuedJob.priority.desc(), QueuedJob.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )

            job = session.exec(stmt).first()

            if job:
                # Mark as running
                job.mark_running()
                session.add(job)
                session.commit()
                session.refresh(job)

            return job

    def get_queue_stats(self, queue_name: str | None = None) -> dict:
        """Get queue statistics (counts by status)."""
        with self._get_session() as session:
            stmt = select(QueuedJob.status, func.count(QueuedJob.id).label('count'))

            if queue_name:
                stmt = stmt.where(QueuedJob.queue_name == queue_name)

            stmt = stmt.group_by(QueuedJob.status)
            results = session.exec(stmt).all()

            stats = {
                'queued': 0,
                'running': 0,
                'success': 0,
                'failed': 0,
            }

            for status, count in results:
                stats[status] = count

            if queue_name:
                stats['queue_name'] = queue_name

            return stats

    def cancel_job(self, job_id: int) -> bool:
        """Cancel a queued job."""
        with self._get_session() as session:
            job = session.get(QueuedJob, job_id)

            if job and job.status == 'queued':
                job.status = 'failed'
                job.error = 'Cancelled by user'
                session.add(job)
                session.commit()
                return True

            return False

    def retry_failed_jobs(self, queue_name: str | None = None, max_jobs: int | None = None) -> int:
        """Retry failed jobs by resetting them to queued status."""
        with self._get_session() as session:
            stmt = select(QueuedJob).where(QueuedJob.status == 'failed')

            if queue_name:
                stmt = stmt.where(QueuedJob.queue_name == queue_name)

            if max_jobs:
                stmt = stmt.limit(max_jobs)

            jobs = session.exec(stmt).all()

            for job in jobs:
                job.status = 'queued'
                job.error = ''
                job.traceback = ''
                job.retry_count = 0
                session.add(job)

            session.commit()
            return len(jobs)

    def get_due_scheduled_tasks(self):
        """Get scheduled tasks that are due to run."""
        with self._get_session() as session:
            stmt = (
                select(ScheduledTask)
                .where(
                    and_(
                        ScheduledTask.enabled == True,
                        ScheduledTask.next_run_at <= datetime.now(UTC)
                    )
                )
                .order_by(ScheduledTask.next_run_at)
            )

            return list(session.exec(stmt).all())

    def create_scheduled_task(
        self,
        name: str,
        task_path: str,
        cron_expression: str,
        queue_name: str,
        priority: int,
        enabled: bool = True,
    ):
        """Create a new scheduled task."""
        task = ScheduledTask(
            name=name,
            task_path=task_path,
            cron_expression=cron_expression,
            queue_name=queue_name,
            priority=priority,
            enabled=enabled,
        )

        with self._get_session() as session:
            session.add(task)
            session.commit()
            session.refresh(task)

        return task

    def get_worker_heartbeats(self, active_only: bool = True):
        """Get worker heartbeats."""
        with self._get_session() as session:
            stmt = select(Worker)

            if active_only:
                threshold = datetime.now(UTC) - timedelta(seconds=60)
                stmt = stmt.where(Worker.last_heartbeat >= threshold)

            stmt = stmt.order_by(Worker.last_heartbeat.desc())

            return list(session.exec(stmt).all())

    def update_worker_heartbeat(self, worker_id: str, status: str, current_job_id: int | None = None, jobs_processed: int | None = None):
        """Update or create worker heartbeat."""
        # import socket  # moved to top-level

        with self._get_session() as session:
            worker = session.get(Worker, worker_id)

            if worker:
                worker.status = status
                worker.current_job_id = current_job_id
                worker.last_heartbeat = datetime.now(UTC)
                if jobs_processed is not None:
                    worker.jobs_processed = jobs_processed
            else:
                worker = Worker(
                    id=worker_id,
                    node_id=socket.gethostname(),
                    pid=os.getpid(),
                    status=status,
                    current_job_id=current_job_id,
                    last_heartbeat=datetime.now(UTC),
                )

            session.add(worker)
            session.commit()

    def cleanup_jobs(
        self,
        status: str | None = None,
        max_age_days: int | None = None,
        max_count: int | None = None,
        queue_name: str | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Clean up old jobs based on retention policy."""
        with self._get_session() as session:
            stmt = delete(QueuedJob)

            if status:
                stmt = stmt.where(QueuedJob.status == status)

            if queue_name:
                stmt = stmt.where(QueuedJob.queue_name == queue_name)

            if max_age_days:
                cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
                stmt = stmt.where(QueuedJob.created_at < cutoff)

            if dry_run:
                # Count without deleting
                # from sqlmodel import select, func  # moved to top-level
                count_stmt = select(func.count(QueuedJob.id))
                if status:
                    count_stmt = count_stmt.where(QueuedJob.status == status)
                if queue_name:
                    count_stmt = count_stmt.where(QueuedJob.queue_name == queue_name)
                if max_age_days:
                    count_stmt = count_stmt.where(QueuedJob.created_at < cutoff)
                count = session.exec(count_stmt).one()
                return {'deleted': 0, 'count': count}

            result = session.exec(stmt)
            session.commit()

            return {'deleted': result.rowcount, 'count': result.rowcount}

    def cleanup_jobs_by_count(
        self,
        status: str | None = None,
        keep_count: int = 1000,
        queue_name: str | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Clean up jobs by keeping only the most recent N jobs."""
        with self._get_session() as session:
            # Get IDs of jobs to keep
            stmt = select(QueuedJob.id)

            if status:
                stmt = stmt.where(QueuedJob.status == status)

            if queue_name:
                stmt = stmt.where(QueuedJob.queue_name == queue_name)

            stmt = stmt.order_by(QueuedJob.created_at.desc()).limit(keep_count)
            keep_ids = list(session.exec(stmt).all())

            # Delete jobs not in keep list
            delete_stmt = delete(QueuedJob)

            if status:
                delete_stmt = delete_stmt.where(QueuedJob.status == status)

            if queue_name:
                delete_stmt = delete_stmt.where(QueuedJob.queue_name == queue_name)

            if keep_ids:
                delete_stmt = delete_stmt.where(~QueuedJob.id.in_(keep_ids))

            if dry_run:
                # Count without deleting — convert delete to count query
                # from sqlmodel import func  # moved to top-level
                count_stmt = select(func.count(QueuedJob.id))
                if status:
                    count_stmt = count_stmt.where(QueuedJob.status == status)
                if queue_name:
                    count_stmt = count_stmt.where(QueuedJob.queue_name == queue_name)
                if keep_ids:
                    count_stmt = count_stmt.where(~QueuedJob.id.in_(keep_ids))
                count = session.exec(count_stmt).one()
                return {'deleted': 0, 'count': count, 'kept': len(keep_ids)}

            result = session.exec(delete_stmt)
            session.commit()

            return {'deleted': result.rowcount, 'count': result.rowcount, 'kept': len(keep_ids)}

    def get_database_stats(self) -> dict:
        """Get database statistics."""
        with self._get_session() as session:
            # Job counts
            job_count_stmt = (
                select(QueuedJob.status, func.count(QueuedJob.id).label('count'))
                .group_by(QueuedJob.status)
            )
            job_counts = {status: count for status, count in session.exec(job_count_stmt).all()}

            # Registry counts
            registry_count_stmt = (
                select(JobRegistry.registry_type, func.count(JobRegistry.id).label('count'))
                .group_by(JobRegistry.registry_type)
            )
            registry_counts = {registry_type: count for registry_type, count in session.exec(registry_count_stmt).all()}

            # Total counts
            total_jobs = session.exec(select(func.count(QueuedJob.id))).one()
            total_registries = session.exec(select(func.count(JobRegistry.id))).one()
            total_scheduled_tasks = session.exec(select(func.count(ScheduledTask.id))).one()
            enabled_scheduled_tasks = session.exec(
                select(func.count(ScheduledTask.id)).where(ScheduledTask.enabled == True)
            ).one()
            total_workers = session.exec(select(func.count(Worker.id))).one()

            stats = {
                'total_jobs': total_jobs,
                'job_counts': job_counts,
                'total_registries': total_registries,
                'registry_counts': registry_counts,
                'total_scheduled_tasks': total_scheduled_tasks,
                'enabled_scheduled_tasks': enabled_scheduled_tasks,
                'total_workers': total_workers,
            }

            return stats

    def vacuum_database(self) -> dict:
        """Run database vacuum/optimize (PostgreSQL VACUUM)."""
        # from sqlalchemy import text  # moved to top-level

        try:
            with self._get_session() as session:
                session.exec(text("VACUUM ANALYZE sqlery_queued_job"))
                session.exec(text("VACUUM ANALYZE sqlery_scheduled_task"))
                session.exec(text("VACUUM ANALYZE sqlery_registry"))
                session.exec(text("VACUUM ANALYZE sqlery_worker"))
                session.commit()

            return {'success': True, 'message': 'Database vacuumed successfully'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def add_job_to_registry(
        self,
        job_id: int,
        registry_type: str,
        metadata: dict | None = None,
    ):
        """Add job to a registry for lifecycle tracking."""
        entry = JobRegistry(
            job_id=job_id,
            registry_type=registry_type,
            extra_data=metadata or {},
        )

        with self._get_session() as session:
            session.add(entry)
            session.commit()

    def remove_job_from_registry(self, job_id: int, registry_type: str):
        """Remove job from a registry."""
        with self._get_session() as session:
            stmt = (
                select(JobRegistry)
                .where(
                    and_(
                        JobRegistry.job_id == job_id,
                        JobRegistry.registry_type == registry_type,
                        JobRegistry.exited_at == None
                    )
                )
            )

            entries = session.exec(stmt).all()

            for entry in entries:
                entry.exited_at = datetime.now(UTC)
                session.add(entry)

            session.commit()

    def get_registry_jobs(
        self,
        registry_type: str,
        queue_name: str | None = None,
        limit: int | None = None,
    ) -> list:
        """Get jobs in a specific registry."""
        with self._get_session() as session:
            stmt = (
                select(JobRegistry)
                .where(
                    and_(
                        JobRegistry.registry_type == registry_type,
                        JobRegistry.exited_at == None
                    )
                )
                .join(QueuedJob, QueuedJob.id == JobRegistry.job_id)
            )

            if queue_name:
                stmt = stmt.where(QueuedJob.queue_name == queue_name)

            if limit:
                stmt = stmt.limit(limit)

            entries = session.exec(stmt).all()

            # Fetch jobs for entries
            jobs = []
            for entry in entries:
                job = session.get(QueuedJob, entry.job_id)
                if job:
                    jobs.append(job)

            return jobs

    def cleanup_registry(
        self,
        registry_type: str | None = None,
        max_age_days: int | None = None,
    ) -> dict:
        """Clean up old registry entries."""
        with self._get_session() as session:
            stmt = delete(JobRegistry)

            if registry_type:
                stmt = stmt.where(JobRegistry.registry_type == registry_type)

            if max_age_days:
                cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
                stmt = stmt.where(JobRegistry.entered_at < cutoff)

            result = session.exec(stmt)
            session.commit()

            return {'deleted': result.rowcount}

    def get_job_by_id(self, job_id: int):
        """Get job by ID."""
        with self._get_session() as session:
            return session.get(QueuedJob, job_id)

    def mark_job_success(self, job_id: int, output: str = ""):
        """Mark job as successful."""
        with self._get_session() as session:
            job = session.get(QueuedJob, job_id)

            if job:
                job.mark_success(output=output)
                session.add(job)
                session.commit()
                session.refresh(job)

            return job

    def mark_job_failed(self, job_id: int, error: str, traceback: str = ""):
        """Mark job as failed."""
        with self._get_session() as session:
            job = session.get(QueuedJob, job_id)

            if job:
                job.mark_failed(error=error, traceback=traceback)
                session.add(job)
                session.commit()
                session.refresh(job)

            return job

    def mark_job_archived(self, job_id: int):
        """Mark a failed job as archived (a retry has been created for it)."""
        with self._get_session() as session:
            job = session.get(QueuedJob, job_id)
            if job and job.status == 'failed':
                job.status = 'archived'
                session.add(job)
                session.commit()

    def cascade_ancestor_status(self, job_id: int, status: str):
        """Walk parent_job_id chain, set all ancestors to given status."""
        with self._get_session() as session:
            job = session.get(QueuedJob, job_id)
            current_id = job.parent_job_id if job else None
            while current_id:
                ancestor = session.get(QueuedJob, current_id)
                if not ancestor:
                    break
                ancestor.status = status
                session.add(ancestor)
                current_id = ancestor.parent_job_id
            session.commit()

    def has_pending_job_for_scheduled_task(self, task_id: int) -> bool:
        """Check if scheduled task has pending jobs."""
        with self._get_session() as session:
            stmt = (
                select(func.count(QueuedJob.id))
                .where(
                    and_(
                        QueuedJob.scheduled_task_id == task_id,
                        QueuedJob.status.in_(['queued', 'running'])
                    )
                )
            )

            count = session.exec(stmt).one()
            return count > 0

    def update_scheduled_task_next_run(self, task_id: int, next_run_at: datetime):
        """Update scheduled task's next run time."""
        with self._get_session() as session:
            task = session.get(ScheduledTask, task_id)

            if task:
                task.next_run_at = next_run_at
                session.add(task)
                session.commit()

    def update_scheduled_task(self, task_id: int, **updates) -> Any:
        """Update scheduled task fields."""
        with self._get_session() as session:
            task = session.get(ScheduledTask, task_id)

            if task:
                for key, value in updates.items():
                    setattr(task, key, value)

                session.add(task)
                session.commit()
                session.refresh(task)

            return task

    def delete_scheduled_task(self, task_id: int) -> bool:
        """Delete scheduled task."""
        with self._get_session() as session:
            task = session.get(ScheduledTask, task_id)

            if task:
                session.delete(task)
                session.commit()
                return True

            return False

    def get_scheduled_tasks(self, enabled_only: bool = False) -> list:
        """Get all scheduled tasks."""
        with self._get_session() as session:
            stmt = select(ScheduledTask)

            if enabled_only:
                stmt = stmt.where(ScheduledTask.enabled == True)

            stmt = stmt.order_by(ScheduledTask.name)

            return list(session.exec(stmt).all())

    def get_scheduled_task(self, task_id: int):
        """Get scheduled task by ID."""
        with self._get_session() as session:
            return session.get(ScheduledTask, task_id)

    def get_running_jobs(self, queue_name: str | None = None) -> list:
        """Get currently running jobs."""
        with self._get_session() as session:
            stmt = select(QueuedJob).where(QueuedJob.status == 'running')

            if queue_name:
                stmt = stmt.where(QueuedJob.queue_name == queue_name)

            return list(session.exec(stmt).all())

    def has_running_jobs_in_queue(self, queue_name: str, exclude_job_id: int | None = None) -> bool:
        """Check if queue has running jobs."""
        with self._get_session() as session:
            stmt = (
                select(func.count(QueuedJob.id))
                .where(
                    and_(
                        QueuedJob.queue_name == queue_name,
                        QueuedJob.status == 'running'
                    )
                )
            )

            if exclude_job_id:
                stmt = stmt.where(QueuedJob.id != exclude_job_id)

            count = session.exec(stmt).one()
            return count > 0

    def update_job_child_pid(self, job_id: int, child_pid: int):
        """Store the forked child PID on the job row."""
        with self._get_session() as session:
            job = session.get(QueuedJob, job_id)
            if job:
                job.child_pid = child_pid
                session.add(job)
                session.commit()

    def delete_worker_registration(self, worker_id: str) -> int:
        """Delete stale Worker row from a previous crash."""
        with self._get_session() as session:
            worker = session.get(Worker, worker_id)
            if worker:
                session.delete(worker)
                session.commit()
                return 1
            return 0

    def release_claimed_job(self, job, worker_id: str, status: str, jobs_processed: int = 0, **kwargs):
        """Release a job after processing and update worker state."""
        with self._get_session() as session:
            db_job = session.get(QueuedJob, job.id)
            if db_job:
                db_job.status = status
                db_job.finished_at = datetime.now(UTC)
                if db_job.started_at:
                    db_job.duration_seconds = (db_job.finished_at - db_job.started_at).total_seconds()
                for key, value in kwargs.items():
                    if hasattr(db_job, key):
                        setattr(db_job, key, value)
                session.add(db_job)

            # Update worker back to idle
            worker = session.get(Worker, worker_id)
            if worker:
                worker.status = 'idle'
                worker.current_job_id = None
                worker.jobs_processed = jobs_processed
                worker.last_heartbeat = datetime.now(UTC)
                session.add(worker)

            session.commit()
            if db_job:
                session.refresh(db_job)
            return db_job

    def count_running_with_tag(self, tag: str) -> int:
        """Count currently running jobs with the given tag."""
        with self._get_session() as session:
            stmt = (
                select(func.count(QueuedJob.id))
                .where(
                    and_(
                        QueuedJob.status == 'running',
                        QueuedJob.tags.contains([tag]),
                    )
                )
            )
            return session.exec(stmt).one()

    def count_started_with_tag_since(self, tag: str, threshold: datetime) -> int:
        """Count jobs with the given tag that started since threshold."""
        with self._get_session() as session:
            stmt = (
                select(func.count(QueuedJob.id))
                .where(
                    and_(
                        QueuedJob.status.in_(['running', 'success', 'failed']),
                        QueuedJob.tags.contains([tag]),
                        QueuedJob.started_at >= threshold,
                        QueuedJob.started_at != None,
                    )
                )
            )
            return session.exec(stmt).one()

    def get_expired_ttl_jobs(self) -> list:
        """Get queued jobs whose TTL has expired."""
        with self._get_session() as session:
            stmt = (
                select(QueuedJob)
                .where(
                    and_(
                        QueuedJob.status == 'queued',
                        QueuedJob.ttl != None,
                    )
                )
            )
            now = datetime.now(UTC)
            expired = []
            for job in session.exec(stmt).all():
                if job.created_at + timedelta(seconds=job.ttl) < now:
                    expired.append(job)
            return expired

    def acquire_tag_locks(self, tags: list[str]) -> None:
        """Acquire exclusive locks on tag coordination rows (PostgreSQL)."""
        # SQLAlchemy backend uses PostgreSQL advisory locks or SELECT FOR UPDATE
        # For now, the transaction isolation provides basic safety
        pass

    def get_claimable_jobs(
        self,
        queues: list[str],
        priority_weights: dict[str, int] | None = None,
        limit: int = 1,
    ) -> list:
        """Get next claimable jobs ordered by priority."""
        with self._get_session() as session:
            now = datetime.now(UTC)
            stmt = (
                select(QueuedJob)
                .where(
                    and_(
                        QueuedJob.queue_name.in_(queues),
                        QueuedJob.status == 'queued',
                        or_(
                            QueuedJob.scheduled_at == None,
                            QueuedJob.scheduled_at <= now,
                        ),
                    )
                )
                .order_by(QueuedJob.priority.desc(), QueuedJob.created_at)
                .with_for_update(skip_locked=True)
                .limit(limit)
            )
            return list(session.exec(stmt).all())

    def atomic_claim_job(self, job, worker) -> bool:
        """Atomically claim a specific job for a worker."""
        with self._get_session() as session:
            db_job = session.get(QueuedJob, job.id)
            if db_job and db_job.status == 'queued':
                db_job.mark_running()
                session.add(db_job)
                session.commit()
                return True
            return False

    def claim_due_scheduled_task(self, task_id: int):
        """Atomically claim a scheduled task for processing."""
        with self._get_session() as session:
            now = datetime.now(UTC)
            stmt = (
                select(ScheduledTask)
                .where(
                    and_(
                        ScheduledTask.id == task_id,
                        ScheduledTask.enabled == True,
                        ScheduledTask.next_run_at <= now,
                    )
                )
                .with_for_update(skip_locked=True)
            )
            task = session.exec(stmt).first()
            return task

    def release_job(self, job_id: int):
        """Release a claimed job back to queued status."""
        with self._get_session() as session:
            job = session.get(QueuedJob, job_id)

            if job:
                job.status = 'queued'
                job.started_at = None
                job.worker_pid = None
                session.add(job)
                session.commit()

    def get_jobs(
        self,
        status: str | None = None,
        queue_name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list:
        """Get jobs with optional filtering and pagination."""
        with self._get_session() as session:
            stmt = select(QueuedJob)

            if status:
                stmt = stmt.where(QueuedJob.status == status)

            if queue_name:
                stmt = stmt.where(QueuedJob.queue_name == queue_name)

            # Order by priority (desc) and created_at (asc)
            stmt = stmt.order_by(QueuedJob.priority.desc(), QueuedJob.created_at)

            # Apply pagination
            stmt = stmt.limit(limit).offset(offset)

            return list(session.exec(stmt).all())

    def count_jobs(
        self,
        status: str | None = None,
        queue_name: str | None = None,
    ) -> int:
        """Count jobs with optional filtering."""
        with self._get_session() as session:
            stmt = select(func.count(QueuedJob.id))

            if status:
                stmt = stmt.where(QueuedJob.status == status)

            if queue_name:
                stmt = stmt.where(QueuedJob.queue_name == queue_name)

            return session.exec(stmt).one()
