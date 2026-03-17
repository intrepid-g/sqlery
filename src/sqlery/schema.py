"""Database schema management for sqlery.

Provides SQL schema constants. Table creation is handled by SQLAlchemyBackend
via SQLModel.metadata.create_all() — see fastapi_sqlery/backend.py.
"""

# DEPRECATED: backends.base no longer exported. Use compat.initialize() for table creation.
# from .backends.base import AsyncStorageBackend, SyncStorageBackend

# SQL schema definitions
QUEUED_JOB_TABLE_SQLITE = """
CREATE TABLE IF NOT EXISTS sqlery_queued_job (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_path TEXT NOT NULL,
    kwargs TEXT DEFAULT '{}',
    queue_name TEXT DEFAULT 'default',
    priority INTEGER DEFAULT 0,
    status TEXT DEFAULT 'queued',
    scheduled_at TIMESTAMP,
    max_retries INTEGER DEFAULT 0,
    retry_count INTEGER DEFAULT 0,
    retry_backoff REAL DEFAULT 1.0,
    allow_parallel BOOLEAN DEFAULT 0,
    timeout_seconds INTEGER,
    worker_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    duration_seconds REAL,
    output TEXT DEFAULT '',
    error TEXT DEFAULT '',
    traceback TEXT DEFAULT '',
    termination_reason TEXT DEFAULT '',
    runs TEXT DEFAULT '[]',
    worker_pid INTEGER
)
"""

QUEUED_JOB_TABLE_POSTGRESQL = """
CREATE TABLE IF NOT EXISTS sqlery_queued_job (
    id SERIAL PRIMARY KEY,
    task_path TEXT NOT NULL,
    kwargs TEXT DEFAULT '{}',
    queue_name TEXT DEFAULT 'default',
    priority INTEGER DEFAULT 0,
    status TEXT DEFAULT 'queued',
    scheduled_at TIMESTAMP,
    max_retries INTEGER DEFAULT 0,
    retry_count INTEGER DEFAULT 0,
    retry_backoff REAL DEFAULT 1.0,
    allow_parallel BOOLEAN DEFAULT FALSE,
    timeout_seconds INTEGER,
    worker_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    duration_seconds REAL,
    output TEXT DEFAULT '',
    error TEXT DEFAULT '',
    traceback TEXT DEFAULT '',
    termination_reason TEXT DEFAULT '',
    runs TEXT DEFAULT '[]',
    worker_pid INTEGER
)
"""

WORKER_TABLE_SQLITE = """
CREATE TABLE IF NOT EXISTS sqlery_worker (
    id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL,
    pid INTEGER NOT NULL,
    status TEXT DEFAULT 'idle',
    current_job_id INTEGER,
    last_heartbeat TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    jobs_processed INTEGER DEFAULT 0
)
"""

WORKER_TABLE_POSTGRESQL = """
CREATE TABLE IF NOT EXISTS sqlery_worker (
    id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL,
    pid INTEGER NOT NULL,
    status TEXT DEFAULT 'idle',
    current_job_id INTEGER,
    last_heartbeat TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    jobs_processed INTEGER DEFAULT 0
)
"""

SCHEDULED_TASK_TABLE_SQLITE = """
CREATE TABLE IF NOT EXISTS sqlery_scheduled_task (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    task_path TEXT NOT NULL,
    cron_expression TEXT NOT NULL,
    queue_name TEXT DEFAULT 'default',
    priority INTEGER DEFAULT 0,
    enabled BOOLEAN DEFAULT 1,
    last_run_at TIMESTAMP,
    next_run_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

SCHEDULED_TASK_TABLE_POSTGRESQL = """
CREATE TABLE IF NOT EXISTS sqlery_scheduled_task (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    task_path TEXT NOT NULL,
    cron_expression TEXT NOT NULL,
    queue_name TEXT DEFAULT 'default',
    priority INTEGER DEFAULT 0,
    enabled BOOLEAN DEFAULT TRUE,
    last_run_at TIMESTAMP,
    next_run_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

REGISTRY_TABLE_SQLITE = """
CREATE TABLE IF NOT EXISTS sqlery_registry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    registry_type TEXT NOT NULL,
    entered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    exited_at TIMESTAMP,
    metadata TEXT DEFAULT '{}',
    FOREIGN KEY (job_id) REFERENCES sqlery_queued_job(id) ON DELETE CASCADE
)
"""

REGISTRY_TABLE_POSTGRESQL = """
CREATE TABLE IF NOT EXISTS sqlery_registry (
    id SERIAL PRIMARY KEY,
    job_id INTEGER NOT NULL,
    registry_type TEXT NOT NULL,
    entered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    exited_at TIMESTAMP,
    metadata TEXT DEFAULT '{}',
    FOREIGN KEY (job_id) REFERENCES sqlery_queued_job(id) ON DELETE CASCADE
)
"""

# =============================================================================
# INDEX DEFINITIONS (SQ-36, SQ-37)
# =============================================================================
# These indexes provide 10-100x performance improvement on hot paths:
# - Job claiming: O(n) -> O(log n)
# - Scheduled job check: O(n) -> O(log n)
# - Worker job lookup: O(n) -> O(1)
# - Cleanup queries: O(n) -> O(log n)
# =============================================================================

# Index SQL statements - works for both SQLite and PostgreSQL
# Using CREATE INDEX IF NOT EXISTS for idempotent operations

INDEXES_SQL = [
    # 1. Job claiming hot path - composite index for optimal claiming
    # Query pattern: WHERE status = 'queued' AND queue_name IN (...) ORDER BY priority DESC, created_at ASC
    """
    CREATE INDEX IF NOT EXISTS idx_jobs_claiming
    ON sqlery_queued_job(status, queue_name, priority DESC, created_at ASC)
    """,
    # 2. Scheduled jobs lookup
    # Query pattern: WHERE scheduled_at <= NOW() AND status = 'queued'
    """
    CREATE INDEX IF NOT EXISTS idx_jobs_scheduled
    ON sqlery_queued_job(scheduled_at, status)
    """,
    # 3. Worker job lookup
    # Query pattern: WHERE worker_id = '...'
    """
    CREATE INDEX IF NOT EXISTS idx_jobs_worker
    ON sqlery_queued_job(worker_id)
    """,
    # 4. Cleanup/reporting queries
    # Query pattern: WHERE created_at < ... AND status IN (...)
    """
    CREATE INDEX IF NOT EXISTS idx_jobs_cleanup
    ON sqlery_queued_job(created_at, status)
    """,
    # 5. Task path lookup (for checking existing jobs for a task)
    # Query pattern: WHERE task_path = '...' AND status IN (...)
    """
    CREATE INDEX IF NOT EXISTS idx_jobs_task_status
    ON sqlery_queued_job(task_path, status)
    """,
    # 6. Registry job lookup
    # Query pattern: WHERE job_id = ... AND registry_type = ...
    """
    CREATE INDEX IF NOT EXISTS idx_registry_job_type
    ON sqlery_registry(job_id, registry_type)
    """,
    # 7. Registry type lookup (for listing all jobs in a registry)
    # Query pattern: WHERE registry_type = ... ORDER BY entered_at DESC
    """
    CREATE INDEX IF NOT EXISTS idx_registry_type_entered
    ON sqlery_registry(registry_type, entered_at DESC)
    """,
    # 8. Registry active entries (exited_at IS NULL)
    # Query pattern: WHERE registry_type = ... AND exited_at IS NULL
    """
    CREATE INDEX IF NOT EXISTS idx_registry_active
    ON sqlery_registry(registry_type, exited_at)
    """,
    # 9. Worker heartbeat lookup
    # Query pattern: WHERE last_heartbeat >= ... ORDER BY last_heartbeat DESC
    """
    CREATE INDEX IF NOT EXISTS idx_worker_heartbeat
    ON sqlery_worker(last_heartbeat DESC)
    """,
    # 10. Worker status lookup
    # Query pattern: WHERE status = '...'
    """
    CREATE INDEX IF NOT EXISTS idx_worker_status
    ON sqlery_worker(status)
    """,
    # 11. Scheduled task due lookup
    # Query pattern: WHERE enabled = TRUE AND next_run_at <= NOW()
    """
    CREATE INDEX IF NOT EXISTS idx_scheduled_task_due
    ON sqlery_scheduled_task(enabled, next_run_at)
    """,
]

# PostgreSQL-specific partial indexes (more efficient for filtered queries)
# These are optional optimizations that only work on PostgreSQL
INDEXES_SQL_POSTGRESQL_PARTIAL = [
    # Partial index for queued jobs only (most common claim query)
    """
    CREATE INDEX IF NOT EXISTS idx_jobs_claiming_queued
    ON sqlery_queued_job(queue_name, priority DESC, created_at ASC)
    WHERE status = 'queued'
    """,
    # Partial index for scheduled jobs that are ready
    """
    CREATE INDEX IF NOT EXISTS idx_jobs_scheduled_ready
    ON sqlery_queued_job(scheduled_at)
    WHERE status = 'queued' AND scheduled_at IS NOT NULL
    """,
    # Partial index for active registry entries
    """
    CREATE INDEX IF NOT EXISTS idx_registry_active_partial
    ON sqlery_registry(registry_type, entered_at DESC)
    WHERE exited_at IS NULL
    """,
    # Partial index for workers with assigned jobs
    """
    CREATE INDEX IF NOT EXISTS idx_jobs_worker_assigned
    ON sqlery_queued_job(worker_id, status)
    WHERE worker_id IS NOT NULL
    """,
]
