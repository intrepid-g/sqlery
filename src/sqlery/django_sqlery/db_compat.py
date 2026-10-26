"""Database compatibility layer for SQLite and PostgreSQL.

Provides database-agnostic operations that work across both backends:
- Atomic job claiming with appropriate locking mechanism per database
- boto3 RDS Data API support for serverless PostgreSQL
"""

import logging

from django.db import connection, transaction
from django.db.models import F
from django.utils import timezone

from .settings import get_setting

try:
    import boto3
except ImportError:
    boto3 = None

logger = logging.getLogger(__name__)


def get_database_vendor():
    """Get the database backend vendor.

    Returns:
        str: 'postgresql', 'sqlite', or other backend name
    """
    return connection.vendor


def is_postgresql():
    """Check if using PostgreSQL database."""
    return get_database_vendor() == 'postgresql'


def is_sqlite():
    """Check if using SQLite database."""
    return get_database_vendor() == 'sqlite'


def assert_in_atomic_block(caller: str) -> None:
    """Guard against select_for_update() being evaluated outside a transaction.

    REGRESSION 2026-05-18 / 2026-06-14: select_for_update() works fine on
    SQLite outside a transaction, but Django raises
    TransactionManagementError on PostgreSQL. That difference means the bug
    only shows up when someone runs against real Postgres -- it happened
    twice because SQLite-only local testing and CI both stayed green.

    This assertion fails immediately, on every database backend, the moment
    a select_for_update() call site is reached without an enclosing
    transaction.atomic() block -- so the mistake is caught in dev/CI
    (including SQLite) instead of only in a live Postgres deployment.

    Args:
        caller: Name of the calling function, used in the error message.

    Raises:
        RuntimeError: If not currently inside a transaction.atomic() block.
    """
    if not connection.in_atomic_block:
        raise RuntimeError(
            f"{caller} calls select_for_update() but is not running inside a "
            "transaction.atomic() block. This works by accident on SQLite and "
            "raises TransactionManagementError on PostgreSQL "
            "(see REGRESSIONS.md 2026-05-18 / 2026-06-14). Wrap the call site "
            "in `with transaction.atomic():`."
        )


def atomic_claim_job_queryset(queryset):
    """Apply database-appropriate locking to job queryset.

    PostgreSQL: Uses SELECT FOR UPDATE SKIP LOCKED for true atomic claiming
    SQLite: Returns unlocked queryset (locking handled by UPDATE below)

    Callers must already be inside a ``transaction.atomic()`` block whenever
    ``select_for_update()`` is actually applied (Postgres and other
    non-SQLite backends) -- enforced by ``assert_in_atomic_block()`` below,
    see REGRESSIONS.md. SQLite never calls ``select_for_update()`` here (it
    is a no-op on that backend), so the guard does not apply there.

    Args:
        queryset: Django QuerySet to apply locking to

    Returns:
        QuerySet: Locked queryset (Postgres) or unlocked queryset (SQLite)
    """
    if is_postgresql():
        # PostgreSQL: SELECT FOR UPDATE SKIP LOCKED
        # Ensures only one worker can claim each job
        assert_in_atomic_block("atomic_claim_job_queryset")
        return queryset.select_for_update(skip_locked=True)
    elif is_sqlite():
        # SQLite: No row-level locking, use UPDATE-based claiming below
        # SKIP LOCKED not supported, so we return unlocked queryset
        # select_for_update() is never called on this path, so the
        # atomic-block guard does not apply here (see REGRESSIONS.md).
        return queryset
    else:
        # Other databases: Try SELECT FOR UPDATE without skip_locked
        assert_in_atomic_block("atomic_claim_job_queryset")
        logger.warning(
            f"Database {get_database_vendor()} may not support SKIP LOCKED. "
            "Using basic SELECT FOR UPDATE."
        )
        return queryset.select_for_update()


def _mirror_claim(job, worker, started_at, expected_version):
    """Reflect a successful claim UPDATE on the in-memory job without another SELECT."""
    job.status = 'running'
    job.worker = worker
    job.started_at = started_at
    job.version = expected_version + 1


def _claim_fenced(job, worker):
    """Claim ``job`` for ``worker``; the single implementation behind both dialect helpers.

    Deleting the job's ready entry (rowcount 1) is the mutex: of any number of claimers only
    one deletes it. The job row is then set running by an update fenced on ``status =
    'queued'`` and on the ``version`` the caller read, and its rowcount is checked. If the
    update loses, the transaction part is rolled back (the ready entry stays) and the claim
    returns False, so a job can never be handed to two claimers.
    """
    from .models import JobReady, QueuedJob  # circular: models.py imports db_compat

    expected_version = job.version or 0
    with transaction.atomic():
        deleted, _ = JobReady.objects.filter(job_id=job.id).delete()
        if deleted < 1:
            return False  # claimed by someone else, cancelled, or not ready
        started_at = timezone.now()
        updated = QueuedJob.objects.filter(
            id=job.id,
            created_at=job.created_at,  # Composite PK: partition key for PG pruning
            status="queued",
            version=expected_version,
        ).update(
            status="running",
            worker=worker,
            started_at=started_at,
            version=F("version") + 1,
        )
        if updated != 1:
            transaction.set_rollback(True)  # keeps the entry: this claimer did not win
            return False
    # The fence proves the row we read is the row we updated: mirror the written values.
    _mirror_claim(job, worker, started_at, expected_version)
    return True


def atomic_claim_job_sqlite(job, worker):
    """Atomically claim a job on SQLite (ready-entry delete + status/version fenced update).

    SQLite has no row locks; the delete of the ready entry is serialised by the database's
    single writer, and the fenced update (``status='queued' AND version=<read>``) is the
    second compare-and-set.

    Args:
        job: QueuedJob instance to claim (must have current version)
        worker: Worker instance claiming the job

    Returns:
        bool: True if claimed successfully, False if another claimer won or the job changed
    """
    return _claim_fenced(job, worker)


def atomic_claim_job_postgres(job, worker):
    """Claim a job on PostgreSQL (ready-entry delete + status/version fenced update).

    The caller's ``SELECT FOR UPDATE SKIP LOCKED`` is not relied on (its lock does not
    survive across transactions): the entry delete decides the race, and the update is
    fenced on ``status='queued'`` and the version the caller read.

    Args:
        job: QueuedJob instance to claim
        worker: Worker instance claiming the job

    Returns:
        bool: True if claimed successfully, False if another claimer won or the job changed
    """
    return _claim_fenced(job, worker)


def atomic_claim_job(job, worker):
    """Atomically claim a job using database-appropriate method.

    Args:
        job: QueuedJob instance to claim
        worker: Worker instance claiming the job

    Returns:
        bool: True if claimed successfully, False otherwise
    """
    if is_postgresql():
        return atomic_claim_job_postgres(job, worker)
    elif is_sqlite():
        return atomic_claim_job_sqlite(job, worker)
    else:
        # Fallback for other databases
        return atomic_claim_job_postgres(job, worker)


# boto3 RDS Data API support
def get_boto3_rds_client():
    """Get boto3 RDS Data API client for serverless PostgreSQL.

    Requires:
        - boto3 installed
        - AWS credentials configured
        - RDS_DATA_API_ARN and RDS_SECRET_ARN in settings

    Returns:
        boto3 client or None if not configured
    """
    try:
        # import boto3  # moved to top-level (optional)
        # from .settings import get_setting  # moved to top-level

        if boto3 is None:
            logger.warning("boto3 not installed, RDS Data API not available")
            return None

        cluster_arn = get_setting('RDS_DATA_API_CLUSTER_ARN')
        secret_arn = get_setting('RDS_DATA_API_SECRET_ARN')
        database_name = get_setting('RDS_DATA_API_DATABASE')

        if not all([cluster_arn, secret_arn, database_name]):
            return None

        return boto3.client('rds-data')
    except Exception as e:
        logger.error(f"Failed to initialize boto3 RDS client: {e}")
        return None


def execute_rds_data_api_query(sql, parameters=None):
    """Execute SQL query using boto3 RDS Data API.

    This is for serverless PostgreSQL (Aurora Serverless) where
    traditional connection pooling isn't optimal.

    Args:
        sql: SQL query string
        parameters: List of parameter dicts for boto3

    Returns:
        dict: Response from RDS Data API
    """
    client = get_boto3_rds_client()
    if not client:
        raise RuntimeError("RDS Data API not configured")

    # from .settings import get_setting  # moved to top-level

    cluster_arn = get_setting('RDS_DATA_API_CLUSTER_ARN')
    secret_arn = get_setting('RDS_DATA_API_SECRET_ARN')
    database_name = get_setting('RDS_DATA_API_DATABASE')

    response = client.execute_statement(
        resourceArn=cluster_arn,
        secretArn=secret_arn,
        database=database_name,
        sql=sql,
        parameters=parameters or []
    )

    return response


def is_using_rds_data_api():
    """Check if configured to use boto3 RDS Data API.

    Returns:
        bool: True if RDS Data API is configured and enabled
    """
    # from .settings import get_setting  # moved to top-level
    return get_setting('USE_RDS_DATA_API', False) and get_boto3_rds_client() is not None
