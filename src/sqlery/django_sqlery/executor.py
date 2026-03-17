"""Task execution engine for sqlery."""

import contextvars
import logging
import traceback as tb

_current_job_var: contextvars.ContextVar = contextvars.ContextVar('current_job', default=None)
from django.db import transaction
from django.utils import timezone
from .models import ScheduledTask, QueuedJob
from .utils import import_task, calculate_next_run
from .db_compat import atomic_claim_job_queryset

logger = logging.getLogger(__name__)


class _RQCompatJob:
    """Thin wrapper that makes a QueuedJob look like an RQ Job for callbacks.

    RQ callbacks receive a job where job.id is the string job_id passed at
    enqueue time. In sqlery, that string lives in job.job_name while job.id
    is an auto-incrementing integer PK. This wrapper proxies all attribute
    access to the real job but overrides .id to return job_name (falling
    back to str(pk) when no job_name was set).
    """

    def __init__(self, job):
        object.__setattr__(self, '_job', job)

    @property
    def id(self):
        job = object.__getattribute__(self, '_job')
        return job.job_name if job.job_name else str(job.pk)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, '_job'), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, '_job'), name, value)


class TaskExecutor:
    """Executes scheduled tasks and processes queued jobs."""

    # ===== Scheduled Task Methods =====

    def get_due_tasks(self):
        """Get all enabled scheduled tasks that are due to run.

        Returns:
            QuerySet: Tasks where next_run_at <= now
        """
        now = timezone.now()
        return ScheduledTask.objects.filter(enabled=True, next_run_at__lte=now)

    def run_due_tasks(self):
        """Find due scheduled tasks and enqueue jobs for them with atomic claiming.

        Uses SELECT ... FOR UPDATE SKIP LOCKED to atomically claim scheduled tasks,
        preventing duplicate enqueueing across multiple scheduler instances.

        Returns:
            list: QueuedJob instances created
        """
        now = timezone.now()
        jobs = []

        # Get all due tasks - we'll claim them atomically one by one
        due_task_ids = list(
            ScheduledTask.objects.filter(
                enabled=True, next_run_at__lte=now
            ).values_list('id', flat=True)
        )

        logger.info(f"Found {len(due_task_ids)} due scheduled tasks")

        # Process each task atomically to prevent duplicate enqueueing
        for task_id in due_task_ids:
            with transaction.atomic():
                # Atomically claim the task using SELECT FOR UPDATE SKIP LOCKED
                try:
                    task = atomic_claim_job_queryset(
                        ScheduledTask.objects.filter(
                            id=task_id,
                            enabled=True,
                            next_run_at__lte=now
                        )
                    ).get()
                except ScheduledTask.DoesNotExist:
                    # Task was claimed by another scheduler or no longer due
                    continue

                # Within the same transaction, check and create job
                job = self._enqueue_for_scheduled_task(task)
                if job:
                    jobs.append(job)

        return jobs

    def _enqueue_for_scheduled_task(self, task):
        """Enqueue a job for a scheduled task.

        Args:
            task: ScheduledTask instance

        Returns:
            QueuedJob: The created job, or None if already queued
        """
        # Check repeat limit for interval tasks
        if task.schedule_type == "interval" and task.repeat is not None:
            total_enqueued = task.jobs.count()
            if total_enqueued >= task.repeat:
                logger.info(
                    f"Scheduled task '{task.name}' reached repeat limit ({task.repeat}), disabling"
                )
                task.enabled = False
                task.next_run_at = None
                task.save(update_fields=["enabled", "next_run_at"])
                return None

        # Check if already queued
        existing_queued = QueuedJob.objects.filter(
            scheduled_task=task, status__in=["queued", "running"]
        ).exists()

        if existing_queued:
            logger.info(
                f"Scheduled task '{task.name}' already has queued/running job, skipping"
            )
            return None

        # Create queued job with task_kwargs
        # job = QueuedJob.objects.create(
        #     task_path=task.task_path,
        #     queue_name=task.queue_name,
        #     priority=task.priority,
        #     scheduled_task=task,
        # )
        job = QueuedJob.objects.create(
            task_path=task.task_path,
            kwargs=task.get_kwargs_dict(),
            queue_name=task.queue_name,
            priority=task.priority,
            scheduled_task=task,
        )

        # Update next run time based on schedule type
        with transaction.atomic():
            task.refresh_from_db()
            update_fields = ["next_run_at"]

            # task.next_run_at = calculate_next_run(
            #     task.cron_expression, base_time=timezone.now()
            # )
            if task.schedule_type == "cron":
                task.next_run_at = calculate_next_run(
                    task.cron_expression, base_time=timezone.now()
                )
            elif task.schedule_type == "interval":
                from datetime import timedelta
                task.next_run_at = timezone.now() + timedelta(
                    seconds=task.get_interval_seconds()
                )
            elif task.schedule_type == "once":
                task.enabled = False
                task.next_run_at = None
                update_fields.append("enabled")

            task.save(update_fields=update_fields)

        logger.info(
            f"Enqueued job for scheduled task '{task.name}' in queue '{task.queue_name}'"
        )
        return job

    # ===== Queue Processing Methods =====

    def get_queued_jobs(self, queue_name=None, limit=None):
        """Get queued jobs ready for execution with atomic row locking.

        Uses SELECT ... FOR UPDATE SKIP LOCKED to atomically claim jobs,
        preventing duplicate execution across multiple workers.

        Args:
            queue_name (str, optional): Filter by queue name
            limit (int, optional): Maximum number of jobs to return

        Returns:
            QuerySet: Queued jobs ordered by priority and creation time,
                     locked for update by this transaction

        Note:
            Requires Postgres for SKIP LOCKED support. On other databases,
            falls back to SELECT FOR UPDATE which may block instead of skip.
        """
        from django.db.models import Q

        now = timezone.now()

        # Filter: status=queued AND (scheduled_at is NULL OR scheduled_at <= now)
        # Use database-appropriate locking for atomic job claiming
        queryset = atomic_claim_job_queryset(QueuedJob.objects).filter(
            status="queued"
        ).filter(
            Q(scheduled_at__isnull=True) | Q(scheduled_at__lte=now)
        ).order_by(
            "-priority", "created_at"
        )

        if queue_name:
            queryset = queryset.filter(queue_name=queue_name)

        if limit:
            queryset = queryset[:limit]

        return queryset

    def can_execute_job(self, job):
        """Check if job can be executed based on queue-level concurrency control.

        If job.allow_parallel is False, checks if another job in the same queue
        is currently running. This prevents concurrent execution within a queue
        while allowing parallel execution across different queues.

        Args:
            job: QueuedJob instance

        Returns:
            bool: True if job can execute
        """
        # If parallel execution allowed for this job, always allow
        if job.allow_parallel:
            return True

        # Check for other running jobs in the SAME QUEUE (not task_path!)
        has_running_in_queue = (
            QueuedJob.objects.filter(
                queue_name=job.queue_name,  # Queue-level check
                status="running"
            )
            .exclude(id=job.id)
            .exists()
        )

        return not has_running_in_queue

    def execute_job(self, job):
        """Execute a single queued job.

        Args:
            job: QueuedJob instance

        Returns:
            QueuedJob: The updated job instance
        """
        # Refresh job state from database
        job.refresh_from_db()

        # Skip if already running (claimed by atomic transaction in run_queue_workers)
        if job.status == "running":
            logger.info(f"Job {job.id} already marked as running, proceeding with execution")
        else:
            # Check concurrency (for direct execute_job calls outside run_queue_workers)
            if not self.can_execute_job(job):
                logger.info(
                    f"Job {job.id} ({job.task_path}) already running elsewhere, skipping"
                )
                return job

            # Mark as running (for non-atomic direct calls)
            job.mark_running()

        try:
            # Set up timeout if specified
            if job.timeout_seconds:
                import signal

                def timeout_handler(signum, frame):
                    raise TimeoutError(
                        f"Job {job.id} exceeded timeout of {job.timeout_seconds} seconds"
                    )

                # Set signal handler and alarm
                signal.signal(signal.SIGALRM, timeout_handler)
                signal.alarm(job.timeout_seconds)
                logger.info(f"Job {job.id} timeout set to {job.timeout_seconds} seconds")

            try:
                # Import task function
                task_func = import_task(job.task_path)

                # Execute with kwargs
                kwargs = job.kwargs if isinstance(job.kwargs, dict) else {}
                logger.info(f"Executing job {job.id}: {job.task_path} with kwargs={kwargs}")
                _token = _current_job_var.set(job)
                try:
                    result = task_func(**kwargs)
                finally:
                    _current_job_var.reset(_token)

                # Mark success
                job.mark_success(output=result or "")
                logger.info(f"Job {job.id} completed successfully")

                # Invoke on_success callback if configured
                if job.on_success_path:
                    try:
                        cb = import_task(job.on_success_path)
                        cb(_RQCompatJob(job), None, result)
                    except Exception:
                        logger.exception(f"on_success callback failed for job {job.id}")
            finally:
                # Cancel alarm if it was set
                if job.timeout_seconds:
                    signal.alarm(0)

            # Update scheduled task last_run_at if applicable
            if job.scheduled_task:
                job.scheduled_task.last_run_at = job.started_at
                job.scheduled_task.save(update_fields=["last_run_at"])

        except Exception as e:
            # Mark failed with termination reason
            error_msg = str(e)
            error_traceback = tb.format_exc()

            # Determine termination reason based on exception type
            if isinstance(e, TimeoutError):
                termination_reason = f"timeout_{job.timeout_seconds}s"
                human_error = f"Job timed out after {job.timeout_seconds} seconds (SIGALRM)"
            else:
                termination_reason = "exception"
                human_error = error_msg

            job.mark_failed(
                error=human_error,
                traceback=error_traceback,
                termination_reason=termination_reason
            )
            logger.error(f"Job {job.id} failed: {human_error}")

            # Invoke on_failure callback if configured
            if job.on_failure_path:
                try:
                    cb = import_task(job.on_failure_path)
                    cb(_RQCompatJob(job), None, None)
                except Exception:
                    logger.exception(f"on_failure callback failed for job {job.id}")

            # Check if job should be retried
            if job.should_retry():
                retry_job = self._retry_job(job)
                logger.info(
                    f"Job {job.id} will be retried as job {retry_job.id} "
                    f"(attempt {retry_job.retry_count + 1}/{retry_job.max_retries + 1})"
                )

        return job

    def _retry_job(self, failed_job):
        """Create a retry job for a failed job.

        Args:
            failed_job: The failed QueuedJob instance

        Returns:
            QueuedJob: The new retry job
        """
        from datetime import timedelta

        # Calculate retry delay
        delay_seconds = failed_job.calculate_retry_delay()
        scheduled_at = timezone.now() + timedelta(seconds=delay_seconds)

        # Create new job for retry (job_name=None to avoid unique constraint collision)
        retry_job = QueuedJob.objects.create(
            task_path=failed_job.task_path,
            kwargs=failed_job.kwargs.copy() if isinstance(failed_job.kwargs, dict) else {},
            queue_name=failed_job.queue_name,
            priority=failed_job.priority,
            scheduled_task=failed_job.scheduled_task,
            retry_count=failed_job.retry_count + 1,
            max_retries=failed_job.max_retries,
            retry_backoff=failed_job.retry_backoff,
            retry_intervals=failed_job.retry_intervals,
            scheduled_at=scheduled_at,
            runs=failed_job.runs.copy() if isinstance(failed_job.runs, list) else [],
            job_name=None,
        )

        logger.info(
            f"Created retry job {retry_job.id} for failed job {failed_job.id}, "
            f"scheduled in {delay_seconds}s (attempt {retry_job.retry_count + 1}/{retry_job.max_retries + 1})"
        )

        return retry_job

    def run_queue_workers(self, queue_name=None, once=False, max_jobs=None):
        """Process jobs from the queue.

        By default, processes one job then spawns another worker for memory safety.
        Use once=True to process jobs until queue is empty without spawning.
        Use max_jobs to limit the number of jobs processed.

        Args:
            queue_name (str, optional): Process specific queue only
            once (bool): If True, process jobs until empty without spawning subprocesses
            max_jobs (int, optional): Maximum number of jobs to process (None = unlimited)

        Returns:
            list: List of processed QueuedJob instances
        """
        logger.info(f"Worker starting (queue={queue_name or 'all'}, once={once}, max_jobs={max_jobs})")

        # Clean up stale jobs before processing (crashed workers)
        self._cleanup_stale_jobs(queue_name)

        processed_jobs = []
        jobs_processed = 0

        while True:
            # Check max_jobs limit
            if max_jobs is not None and jobs_processed >= max_jobs:
                logger.info(f"Reached max_jobs limit ({max_jobs})")
                break

            # Atomically claim one job
            with transaction.atomic():
                # Get next job (atomically locked via select_for_update)
                queued_jobs = self.get_queued_jobs(queue_name=queue_name, limit=1)

                if not queued_jobs.exists():
                    logger.info("No queued jobs found - worker exiting")
                    break

                job = queued_jobs.first()

                # Check concurrency BEFORE marking as running
                if not self.can_execute_job(job):
                    logger.info(
                        f"Job {job.id} in queue '{job.queue_name}' blocked by running job "
                        f"(allow_parallel={job.allow_parallel}) - leaving queued, worker exiting"
                    )
                    # Leave job as queued - another worker will try later
                    break

                # Mark as running within the same transaction (releases lock after commit)
                job.mark_running()

            # Execute the job (outside transaction, don't hold lock during execution)
            logger.info(f"Worker processing job {job.id}: {job.task_path}")
            processed_job = self.execute_job(job)
            logger.info(
                f"Worker completed job {job.id} with status '{processed_job.status}'"
            )

            processed_jobs.append(processed_job)
            jobs_processed += 1

            # If not in "once" mode, process just one job and spawn next worker
            if not once:
                # Check if more jobs exist - spawn next worker immediately if so
                more_jobs_exist = self.get_queued_jobs(queue_name=queue_name, limit=1).exists()
                if more_jobs_exist:
                    logger.info("More jobs exist - spawning next worker immediately")
                    self._spawn_next_worker(queue_name)
                else:
                    logger.info("No more jobs - worker exiting")
                break

        return processed_jobs

    def _cleanup_stale_jobs(self, queue_name=None):
        """Reset stale jobs stuck in 'running' state (crashed workers).

        Jobs are considered stale if they've been running longer than:
        - 2x their timeout_seconds (if set)
        - 1 hour (default if no timeout)

        Stale jobs are marked as failed with crash error message.

        Args:
            queue_name (str, optional): Only check specific queue
        """
        from datetime import timedelta

        now = timezone.now()

        # Find jobs in running state
        queryset = QueuedJob.objects.filter(status="running")
        if queue_name:
            queryset = queryset.filter(queue_name=queue_name)

        for job in queryset:
            if not job.started_at:
                # Job marked as running but never started - definitely stale
                job.mark_failed(
                    error="Worker crashed before job execution started",
                    traceback="Job was stuck in 'running' state with no started_at timestamp",
                    termination_reason="worker_crashed_before_start"
                )
                logger.warning(f"Cleaned up stale job {job.id} (no started_at)")
                continue

            # Calculate staleness threshold
            if job.timeout_seconds:
                threshold_seconds = job.timeout_seconds * 2
            else:
                threshold_seconds = 3600  # 1 hour default

            running_duration = (now - job.started_at).total_seconds()

            if running_duration > threshold_seconds:
                # Try to kill the worker process if PID is stored and process exists
                kill_method = None
                if job.worker_pid:
                    kill_method = self._kill_worker_process(job.worker_pid)
                    if kill_method:
                        logger.warning(
                            f"Killed hung worker process {job.worker_pid} for job {job.id} using {kill_method}"
                        )

                # Determine termination reason based on what happened
                if kill_method == "SIGTERM":
                    termination_reason = "killed_by_sigterm"
                    error_msg = f"Job forcefully terminated with SIGTERM after exceeding threshold ({int(running_duration)}s running, {threshold_seconds}s threshold)"
                elif kill_method == "SIGKILL":
                    termination_reason = "killed_by_sigkill"
                    error_msg = f"Job forcefully killed with SIGKILL after exceeding threshold ({int(running_duration)}s running, {threshold_seconds}s threshold)"
                else:
                    termination_reason = "worker_crashed_or_oom"
                    error_msg = f"Worker process crashed or was killed by system (running for {int(running_duration)}s, threshold {threshold_seconds}s)"

                job.mark_failed(
                    error=error_msg,
                    traceback="Job was stuck in 'running' state - likely worker process crashed, killed by OOM, or terminated by signal",
                    termination_reason=termination_reason
                )
                logger.warning(
                    f"Cleaned up stale job {job.id} (running {int(running_duration)}s > {threshold_seconds}s threshold)"
                )

                # Retry if configured
                if job.should_retry():
                    retry_job = self._retry_job(job)
                    logger.info(
                        f"Stale job {job.id} will be retried as job {retry_job.id}"
                    )

    def _kill_worker_process(self, pid):
        """Kill a worker process by PID.

        Tries SIGTERM first (graceful), then SIGKILL after 5 seconds.

        Args:
            pid (int): Process ID to kill

        Returns:
            str|None: "SIGTERM" if killed gracefully, "SIGKILL" if force-killed,
                     None if process already dead or error
        """
        import os
        import signal
        import time

        try:
            # Check if process exists
            os.kill(pid, 0)  # Signal 0 checks existence without killing
        except OSError:
            # Process doesn't exist
            return None

        try:
            # Send SIGTERM (graceful shutdown)
            os.kill(pid, signal.SIGTERM)
            logger.info(f"Sent SIGTERM to worker process {pid}")

            # Wait up to 5 seconds for graceful shutdown
            for _ in range(10):
                time.sleep(0.5)
                try:
                    os.kill(pid, 0)
                except OSError:
                    # Process terminated
                    logger.info(f"Worker process {pid} terminated gracefully with SIGTERM")
                    return "SIGTERM"

            # Process still alive after 5s, send SIGKILL
            logger.warning(f"Worker process {pid} did not terminate, sending SIGKILL")
            os.kill(pid, signal.SIGKILL)

            # Wait briefly for SIGKILL
            time.sleep(0.5)
            try:
                os.kill(pid, 0)
            except OSError:
                logger.info(f"Worker process {pid} killed with SIGKILL")
                return "SIGKILL"

            logger.error(f"Failed to kill worker process {pid}")
            return None

        except Exception as e:
            logger.error(f"Error killing worker process {pid}: {e}")
            return None

    def process_one_job(self, queue_name=None):
        """Process exactly one job from the queue (for single-worker daemon mode).

        Args:
            queue_name (str, optional): Process specific queue only

        Returns:
            QueuedJob|None: The processed job, or None if no jobs available
        """
        # Clean up stale jobs before processing
        self._cleanup_stale_jobs(queue_name)

        # Atomically claim one job
        with transaction.atomic():
            # Get next job (atomically locked via select_for_update)
            queued_jobs = self.get_queued_jobs(queue_name=queue_name, limit=1)

            if not queued_jobs.exists():
                return None

            job = queued_jobs.first()

            # Check concurrency BEFORE marking as running
            if not self.can_execute_job(job):
                logger.info(
                    f"Job {job.id} in queue '{job.queue_name}' blocked by running job "
                    f"(allow_parallel={job.allow_parallel}) - leaving queued"
                )
                return None

            # Mark as running within the same transaction
            job.mark_running()

        # Execute the job (outside transaction)
        processed_job = self.execute_job(job)
        return processed_job

    def _spawn_next_worker(self, queue_name=None):
        """Spawn another worker subprocess to process next job.

        Args:
            queue_name (str, optional): Queue name to pass to next worker
        """
        import subprocess
        import sys
        import os
        from .subprocess_executor import get_manage_py_path

        try:
            manage_py = get_manage_py_path()

            # Build command arguments
            cmd = [sys.executable, manage_py, "run_jobs", "--worker-only"]
            if queue_name:
                cmd.extend(["--queue", queue_name])

            # Spawn subprocess (fire-and-forget)
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=os.environ,
                start_new_session=True,
                close_fds=True,
            )

            logger.debug(f"Spawned next worker: {' '.join(cmd)}")
        except Exception as e:
            logger.error(f"Failed to spawn next worker: {e}")
