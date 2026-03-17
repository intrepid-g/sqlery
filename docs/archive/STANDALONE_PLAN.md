# Sqlery Standalone Architecture Plan

**Goal**: Transform sqlery into a truly standalone job queue library (like RQ), with Django and FastAPI as optional integration plugins.

**Version**: 1.0
**Date**: 2025-11-05
**Status**: Planning Phase

---

## Executive Summary

> **IMPORTANT**: Sqlery v3.0 is a greenfield rewrite with **no production users**. We are **NOT constrained by backward compatibility**. Design decisions should prioritize **best practices and optimal architecture** over maintaining compatibility with v2.x.

### Current State (v2.x)

Sqlery is **70% standalone-ready** with excellent architectural foundations:

- ✅ Dual backend system (Django ORM + SQLAlchemy)
- ✅ Framework-agnostic core module (`sqlery/core/`)
- ✅ Abstract compatibility layer (`DatabaseBackend`)
- ✅ Minimal core dependencies (croniter, psycopg2, uuid6)
- ⚠️ Django-centric file organization in main package
- ⚠️ Dual migration systems (Django migrations + Alembic)
- ⚠️ Documentation positions it as "Django package with standalone mode"

### Target State (v3.0)

**Standalone-first design** like RQ:

```python
# Core sqlery - no ORM dependencies
from sqlery import Queue, Worker
from sqlery.decorators import job

q = Queue()

@job(queue='default')
def send_email(to, subject, body):
    # Send email logic
    pass

# Enqueue jobs
q.enqueue(send_email, 'user@example.com', 'Hello', 'Body')

# Run worker
worker = Worker([q])
worker.work()
```

**Django plugin** imports core sqlery:

```python
# Django integration (separate package)
from sqlery import job  # Core sqlery
from sqlery.integrations.django import DjangoBackend

@job(queue='default')
def process_order(order_id):
    from myapp.models import Order
    order = Order.objects.get(id=order_id)
    # Process order
```

**FastAPI plugin** imports core sqlery:

```python
# FastAPI integration (separate package)
from sqlery import Queue, job
from sqlery.integrations.fastapi import FastAPIApp

app = FastAPIApp()

@job(queue='default')
def generate_report(user_id):
    # Generate report
    pass
```

---

## Development Principles

**IMPORTANT: This is a greenfield rewrite. Backward compatibility is NOT a constraint.**

### No Backward Compatibility Required

Sqlery v3.0 is a **complete architectural rewrite** and is **not yet in production use by any external teams**. This gives us complete freedom:

- **Breaking Changes Welcome**: If a better design emerges during implementation, implement it immediately without hesitation
- **No Migration Burden**: We are not constrained by v2.x API decisions or implementation details
- **Clean Slate**: Design the best possible API and architecture without worrying about existing users
- **Fresh Start Mentality**: Treat this as a new project, not a refactor

### Focus on Best Design

Our priorities for v3.0 implementation:

1. **Modern Architecture**: Use current best practices (2025 standards)
2. **Clean API**: Simple, intuitive interfaces inspired by RQ but better
3. **Performance**: Optimize for speed and efficiency without legacy constraints
4. **Maintainability**: Write code that is easy to understand and extend
5. **Type Safety**: Full type hints using modern Python 3.13+ syntax

### What This Means in Practice

- If the current plan suggests a suboptimal approach, **change the plan**
- If maintaining compatibility with v2.x would compromise design quality, **ignore v2.x compatibility**
- If a "migration strategy" section doesn't apply (because there are no users to migrate), **skip it**
- Design decisions should be based on **merit alone**, not backward compatibility concerns

### Version 2.x is Dead, Long Live Version 3.0

The v2.x codebase exists only as:
- A reference for understanding the problem domain
- A source of battle-tested algorithms and patterns
- Inspiration for feature completeness

It is **NOT**:
- A compatibility target
- A constraint on design decisions
- Something we need to provide migration paths from

---

## 1. Architecture Overview

### 1.1 Current Architecture

```
sqlery/
├── src/sqlery/                    # Main package (Django-centric)
│   ├── models.py                  # Django ORM models
│   ├── admin.py                   # Django admin
│   ├── middleware.py              # Django middleware
│   ├── migrations/                # Django migrations (11 files)
│   ├── management/commands/       # Django commands
│   │
│   ├── core/                      # Framework-agnostic ✅
│   │   ├── worker.py
│   │   ├── scheduler.py
│   │   ├── daemon.py
│   │   ├── queue.py
│   │   └── cli.py
│   │
│   ├── django/                    # Django backend
│   │   ├── backend.py
│   │   └── config.py
│   │
│   └── fastapi/                   # Standalone backend (misnamed)
│       ├── backend.py             # SQLAlchemy backend
│       ├── app.py                 # Web UI
│       └── cli.py
│
└── alembic/                       # Alembic migrations
```

**Issues**:
- Django files in main package suggest it's Django-first
- 48 files import Django (some unnecessarily)
- Dual migration systems create maintenance overhead
- "fastapi" directory is misleading (it's actually standalone)

### 1.2 Target Architecture

```
sqlery/
├── src/sqlery/                    # Core package (standalone)
│   ├── __init__.py                # Public API exports
│   ├── queue.py                   # Queue abstraction
│   ├── worker.py                  # Worker implementation
│   ├── job.py                     # Job abstraction
│   ├── decorators.py              # @job decorator
│   ├── scheduler.py               # Cron scheduler
│   ├── registry.py                # Job registries
│   ├── cleanup.py                 # Cleanup utilities
│   ├── cli.py                     # Core CLI (Typer)
│   │
│   ├── backends/                  # Storage backends
│   │   ├── base.py                # Abstract backend interface
│   │   ├── postgres.py            # PostgreSQL backend (raw SQL)
│   │   └── sqlite.py              # SQLite backend (raw SQL)
│   │
│   ├── migrations/                # Raw SQL migrations
│   │   ├── 001_initial.sql
│   │   ├── 002_add_scheduling.sql
│   │   ├── ...
│   │   └── 011_add_version.sql
│   │
│   └── integrations/              # Optional integrations
│       ├── django/                # Django plugin (optional)
│       │   ├── __init__.py
│       │   ├── backend.py         # Django ORM adapter
│       │   ├── models.py          # Django models
│       │   ├── admin.py           # Django admin
│       │   ├── middleware.py
│       │   ├── management/
│       │   └── migrations/        # Generated from core SQL
│       │
│       ├── fastapi/               # FastAPI plugin (optional)
│       │   ├── __init__.py
│       │   ├── app.py             # Web UI
│       │   └── routes.py
│       │
│       └── flask/                 # Future: Flask plugin
│           └── __init__.py
│
├── pyproject.toml                 # Core dependencies only
└── README.md                      # Standalone-first docs
```

**Benefits**:
- Core package has ZERO ORM dependencies
- Django/FastAPI are clearly optional integrations
- Single migration source of truth (raw SQL)
- Framework integrations import from core, not vice versa

---

## 2. Dependencies Strategy

### 2.1 Core Dependencies (Minimal)

```toml
[project]
name = "sqlery"
dependencies = [
    "croniter>=2.0.0",        # Cron parsing (pure Python)
    "databases>=0.8.0",       # Async/sync database abstraction
    "uuid6>=2024.1.0",        # Time-sortable UUIDs
    "typer>=0.9.0",           # CLI framework
    "rich>=13.0.0",           # Terminal formatting
]
```

**Rationale**:
- No ORM dependencies (no SQLAlchemy, no Django)
- `databases` provides unified interface for both sync and async operations
- `databases` supports PostgreSQL, MySQL, SQLite out of the box
- All dependencies are standalone libraries
- Total: ~5 core dependencies

**Why `databases`?**
- Single API for both sync and async workflows
- Connection pooling built-in
- Supports multiple databases (PostgreSQL, MySQL, SQLite)
- Lightweight wrapper around native drivers (psycopg, asyncpg, etc.)
- Well-maintained and production-ready

### 2.2 Optional Features

```toml
[project.optional-dependencies]
# Django integration
django = [
    "django>=4.2",
]

# FastAPI web UI
fastapi = [
    "fastapi>=0.104.0",
    "uvicorn[standard]>=0.24.0",
    "jinja2>=3.1.0",
]

# SQLite support
sqlite = [
    "apsw>=3.40.0",  # Better SQLite driver
]

# Advanced features
http = ["httpx>=0.24.0"]
eventbridge = ["boto3>=1.34.0"]

# Development
dev = [
    "pytest>=7.0.0",
    "pytest-cov>=4.0.0",
    "ruff>=0.1.0",
]
```

### 2.3 Removed Dependencies

**Eliminate from core**:
- ❌ `sqlmodel` - ORM not needed in core
- ❌ `sqlalchemy` - ORM not needed in core
- ❌ `alembic` - Raw SQL migrations instead
- ❌ `fastapi` - Move to optional integration
- ❌ `uvicorn` - Move to optional integration

**Impact**: Core package becomes ~10x lighter, faster to install, easier to audit.

---

## 3. Migration System

### 3.1 Problems with Current Approach

**Dual Migration Systems**:
- Django migrations: 11 files in `sqlery/migrations/`
- Alembic migrations: Config in `alembic/`
- **Issue**: Schema drift risk, maintenance overhead

**ORM Dependencies**:
- Django migrations require Django ORM
- Alembic migrations require SQLAlchemy
- **Issue**: Forces users to install ORM even if they don't use it

### 3.2 Target: Raw SQL Migrations

**Philosophy**: Migrations should be pure SQL, applied by core sqlery without ORM dependencies.

#### Directory Structure

```
src/sqlery/migrations/
├── __init__.py
├── runner.py              # Migration runner (no ORM)
├── 001_initial.sql
├── 002_add_cron_jobs.sql
├── 003_add_retry_logic.sql
├── 004_add_job_state.sql
├── 005_add_job_result.sql
├── 006_add_registry_system.sql
├── 007_add_daemon_manager.sql
├── 008_add_rate_limiting.sql
├── 009_add_webhooks.sql
├── 010_add_optimistic_locking.sql
└── 011_add_version_field.sql
```

#### Migration Format

Each SQL file contains:

```sql
-- Migration: 001_initial
-- Description: Create core tables for job queue
-- Postgres: YES
-- SQLite: YES

-- Up Migration
BEGIN;

CREATE TABLE IF NOT EXISTS sqlery_jobs (
    id UUID PRIMARY KEY,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    queue VARCHAR(255) NOT NULL,
    func_name VARCHAR(512) NOT NULL,
    args JSONB,
    kwargs JSONB,
    status VARCHAR(20) NOT NULL,
    priority INTEGER DEFAULT 0,
    timeout INTEGER,
    result JSONB,
    error JSONB,
    started_at TIMESTAMP WITH TIME ZONE,
    finished_at TIMESTAMP WITH TIME ZONE,
    worker_name VARCHAR(255)
);

CREATE INDEX idx_jobs_queue_status ON sqlery_jobs(queue, status);
CREATE INDEX idx_jobs_created_at ON sqlery_jobs(created_at);

CREATE TABLE IF NOT EXISTS sqlery_migrations (
    id SERIAL PRIMARY KEY,
    migration_name VARCHAR(255) NOT NULL UNIQUE,
    applied_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

COMMIT;

-- Down Migration (optional)
-- DROP TABLE sqlery_jobs;
-- DROP TABLE sqlery_migrations;
```

#### Migration Runner

```python
# src/sqlery/migrations/runner.py
import os
import re
from pathlib import Path
from typing import Protocol

class DatabaseConnection(Protocol):
    """Protocol for database connections."""
    def execute(self, sql: str) -> None: ...
    def fetchall(self, sql: str) -> list[tuple]: ...
    def commit(self) -> None: ...

class MigrationRunner:
    """Run raw SQL migrations without ORM dependencies."""

    def __init__(self, connection: DatabaseConnection):
        self.connection = connection
        self.migrations_dir = Path(__file__).parent

    def get_applied_migrations(self) -> set[str]:
        """Get list of applied migrations from database."""
        try:
            rows = self.connection.fetchall(
                "SELECT migration_name FROM sqlery_migrations ORDER BY id"
            )
            return {row[0] for row in rows}
        except:
            # Table doesn't exist yet
            return set()

    def get_pending_migrations(self) -> list[tuple[str, str]]:
        """Get list of pending migrations to apply."""
        applied = self.get_applied_migrations()
        pending = []

        for sql_file in sorted(self.migrations_dir.glob("*.sql")):
            if sql_file.stem.startswith("_"):
                continue

            migration_name = sql_file.stem
            if migration_name not in applied:
                pending.append((migration_name, sql_file.read_text()))

        return pending

    def apply_migration(self, name: str, sql: str) -> None:
        """Apply a single migration."""
        # Extract UP migration section
        up_match = re.search(r"-- Up Migration\n(.*?)(?=-- Down Migration|$)",
                           sql, re.DOTALL)
        if not up_match:
            raise ValueError(f"No 'Up Migration' section in {name}")

        up_sql = up_match.group(1).strip()

        # Execute migration
        self.connection.execute(up_sql)

        # Record migration
        self.connection.execute(
            "INSERT INTO sqlery_migrations (migration_name) VALUES (%s)",
            (name,)
        )

        self.connection.commit()

    def migrate(self) -> list[str]:
        """Run all pending migrations."""
        pending = self.get_pending_migrations()
        applied = []

        for name, sql in pending:
            self.apply_migration(name, sql)
            applied.append(name)

        return applied
```

#### CLI Command

```bash
# Run migrations
sqlery migrate

# Check migration status
sqlery migrate --status

# Generate new migration
sqlery migrate --create "add_new_field"
```

### 3.3 Framework Integration Migrations

**Django**: Generate Django migrations from core SQL schema

```python
# sqlery/integrations/django/management/commands/sync_migrations.py
from django.core.management import BaseCommand
from sqlery.migrations import MigrationRunner

class Command(BaseCommand):
    """Generate Django migrations from core SQL schema."""

    def handle(self, *args, **options):
        # Read core SQL migrations
        # Generate equivalent Django migration files
        # This is ONE-WAY: SQL → Django (SQL is source of truth)
        pass
```

**FastAPI**: Migrations run automatically by core sqlery (raw SQL).

**Benefits**:
- Single source of truth: Raw SQL files
- No ORM dependency in core
- Django can still use Django migrations (generated from SQL)
- FastAPI uses raw SQL directly
- Schema inspection tools can read SQL directly

---

## 4. Core API Design

### 4.1 Backend Abstraction with Factory Pattern

#### Abstract Base Classes for Sync and Async

```python
# src/sqlery/backends/base.py
from abc import ABC, abstractmethod
from typing import Any, Protocol
from datetime import datetime
from uuid import UUID

class SyncStorageBackend(ABC):
    """Abstract synchronous storage backend for job queue.

    Implementations provide blocking database operations.
    Used by sync workers, CLI commands, and Django integration.
    """

    @abstractmethod
    def create_job(
        self,
        id: UUID,
        queue: str,
        func_name: str,
        args: list[Any],
        kwargs: dict[str, Any],
        **options
    ) -> dict[str, Any]:
        """Create a new job (blocking)."""
        pass

    @abstractmethod
    def claim_job(self, queue: str, worker_name: str) -> dict[str, Any] | None:
        """Atomically claim next available job (blocking)."""
        pass

    @abstractmethod
    def update_job_status(self, job_id: UUID, status: str, **fields) -> None:
        """Update job status and fields (blocking)."""
        pass

    @abstractmethod
    def get_job(self, job_id: UUID) -> dict[str, Any] | None:
        """Retrieve job by ID (blocking)."""
        pass

    @abstractmethod
    def delete_job(self, job_id: UUID) -> None:
        """Delete job (blocking)."""
        pass

    @abstractmethod
    def list_jobs(
        self,
        queue: str | None = None,
        status: str | None = None,
        limit: int = 100
    ) -> list[dict[str, Any]]:
        """List jobs with filters (blocking)."""
        pass

    # ... 40+ more methods for complete job queue operations


class AsyncStorageBackend(ABC):
    """Abstract asynchronous storage backend for job queue.

    Implementations provide non-blocking database operations.
    Used by async workers, FastAPI integration, and async applications.
    """

    @abstractmethod
    async def create_job(
        self,
        id: UUID,
        queue: str,
        func_name: str,
        args: list[Any],
        kwargs: dict[str, Any],
        **options
    ) -> dict[str, Any]:
        """Create a new job (async)."""
        pass

    @abstractmethod
    async def claim_job(self, queue: str, worker_name: str) -> dict[str, Any] | None:
        """Atomically claim next available job (async)."""
        pass

    @abstractmethod
    async def update_job_status(self, job_id: UUID, status: str, **fields) -> None:
        """Update job status and fields (async)."""
        pass

    @abstractmethod
    async def get_job(self, job_id: UUID) -> dict[str, Any] | None:
        """Retrieve job by ID (async)."""
        pass

    @abstractmethod
    async def delete_job(self, job_id: UUID) -> None:
        """Delete job (async)."""
        pass

    @abstractmethod
    async def list_jobs(
        self,
        queue: str | None = None,
        status: str | None = None,
        limit: int = 100
    ) -> list[dict[str, Any]]:
        """List jobs with filters (async)."""
        pass

    # ... 40+ more methods for complete job queue operations
```

#### Backend Factory

```python
# src/sqlery/backends/factory.py
from typing import Literal
from .base import SyncStorageBackend, AsyncStorageBackend
from .sync_backend import SyncDatabaseBackend
from .async_backend import AsyncDatabaseBackend

BackendType = Literal['sync', 'async']

class BackendFactory:
    """Factory for creating storage backends based on workflow type."""

    @staticmethod
    def create_backend(
        connection_string: str,
        backend_type: BackendType = 'sync'
    ) -> SyncStorageBackend | AsyncStorageBackend:
        """Create a backend instance based on type.

        Args:
            connection_string: Database connection URL
            backend_type: 'sync' for blocking operations, 'async' for non-blocking

        Returns:
            Backend instance (sync or async)

        Examples:
            # Sync backend for CLI and Django
            backend = BackendFactory.create_backend(
                'postgresql://localhost/myapp',
                backend_type='sync'
            )

            # Async backend for FastAPI and async workers
            backend = BackendFactory.create_backend(
                'postgresql://localhost/myapp',
                backend_type='async'
            )
        """
        if backend_type == 'sync':
            return SyncDatabaseBackend(connection_string)
        elif backend_type == 'async':
            return AsyncDatabaseBackend(connection_string)
        else:
            raise ValueError(f"Invalid backend_type: {backend_type}")

    @staticmethod
    def create_sync_backend(connection_string: str) -> SyncStorageBackend:
        """Create a synchronous backend (convenience method)."""
        return SyncDatabaseBackend(connection_string)

    @staticmethod
    def create_async_backend(connection_string: str) -> AsyncStorageBackend:
        """Create an asynchronous backend (convenience method)."""
        return AsyncDatabaseBackend(connection_string)
```

### 4.2 Synchronous Backend (using `databases` in sync mode)

```python
# src/sqlery/backends/sync_backend.py
import json
from typing import Any
from uuid import UUID
from databases import Database
from .base import SyncStorageBackend

class SyncDatabaseBackend(SyncStorageBackend):
    """Synchronous database backend using `databases` library.

    Uses `databases` in force_rollback=False mode for synchronous operations.
    Supports PostgreSQL, MySQL, SQLite via unified interface.
    """

    def __init__(self, connection_string: str):
        """Initialize sync backend.

        Args:
            connection_string: Database URL
                - postgresql://user:pass@localhost/db
                - mysql://user:pass@localhost/db
                - sqlite:///path/to/db.sqlite
        """
        self.connection_string = connection_string
        # Database instance - will be connected via connect()
        self.db = Database(connection_string)
        self._connected = False

    def connect(self):
        """Connect to database (blocking)."""
        if not self._connected:
            # Use run_sync helper to connect
            import asyncio
            asyncio.run(self.db.connect())
            self._connected = True

    def disconnect(self):
        """Disconnect from database (blocking)."""
        if self._connected:
            import asyncio
            asyncio.run(self.db.disconnect())
            self._connected = False

    def create_job(
        self,
        id: UUID,
        queue: str,
        func_name: str,
        args: list[Any],
        kwargs: dict[str, Any],
        **options
    ) -> dict[str, Any]:
        """Create job using raw SQL INSERT (blocking)."""
        import asyncio

        query = """
            INSERT INTO sqlery_jobs (
                id, queue, func_name, args, kwargs,
                status, priority, timeout, created_at, updated_at
            )
            VALUES (:id, :queue, :func_name, :args, :kwargs,
                    :status, :priority, :timeout, NOW(), NOW())
            RETURNING *
        """

        values = {
            "id": str(id),
            "queue": queue,
            "func_name": func_name,
            "args": json.dumps(args),
            "kwargs": json.dumps(kwargs),
            "status": "queued",
            "priority": options.get("priority", 0),
            "timeout": options.get("timeout"),
        }

        # Execute query synchronously
        result = asyncio.run(self.db.fetch_one(query, values))
        return dict(result)

    def claim_job(self, queue: str, worker_name: str) -> dict[str, Any] | None:
        """Atomically claim job using SELECT FOR UPDATE SKIP LOCKED (blocking)."""
        import asyncio

        query = """
            UPDATE sqlery_jobs
            SET status = 'started',
                worker_name = :worker_name,
                started_at = NOW(),
                updated_at = NOW()
            WHERE id = (
                SELECT id FROM sqlery_jobs
                WHERE queue = :queue AND status = 'queued'
                ORDER BY priority DESC, created_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING *
        """

        values = {"worker_name": worker_name, "queue": queue}
        result = asyncio.run(self.db.fetch_one(query, values))
        return dict(result) if result else None

    def update_job_status(self, job_id: UUID, status: str, **fields) -> None:
        """Update job status (blocking)."""
        import asyncio

        # Build dynamic SET clause
        set_parts = ["status = :status", "updated_at = NOW()"]
        values = {"job_id": str(job_id), "status": status}

        for key, value in fields.items():
            set_parts.append(f"{key} = :{key}")
            values[key] = value

        query = f"""
            UPDATE sqlery_jobs
            SET {', '.join(set_parts)}
            WHERE id = :job_id
        """

        asyncio.run(self.db.execute(query, values))

    def get_job(self, job_id: UUID) -> dict[str, Any] | None:
        """Retrieve job by ID (blocking)."""
        import asyncio

        query = "SELECT * FROM sqlery_jobs WHERE id = :job_id"
        result = asyncio.run(self.db.fetch_one(query, {"job_id": str(job_id)}))
        return dict(result) if result else None

    def delete_job(self, job_id: UUID) -> None:
        """Delete job (blocking)."""
        import asyncio

        query = "DELETE FROM sqlery_jobs WHERE id = :job_id"
        asyncio.run(self.db.execute(query, {"job_id": str(job_id)}))

    def list_jobs(
        self,
        queue: str | None = None,
        status: str | None = None,
        limit: int = 100
    ) -> list[dict[str, Any]]:
        """List jobs with filters (blocking)."""
        import asyncio

        conditions = []
        values = {"limit": limit}

        if queue:
            conditions.append("queue = :queue")
            values["queue"] = queue

        if status:
            conditions.append("status = :status")
            values["status"] = status

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        query = f"""
            SELECT * FROM sqlery_jobs
            {where_clause}
            ORDER BY created_at DESC
            LIMIT :limit
        """

        results = asyncio.run(self.db.fetch_all(query, values))
        return [dict(row) for row in results]

    # ... implement all other abstract methods
```

### 4.3 Asynchronous Backend (using `databases` in async mode)

```python
# src/sqlery/backends/async_backend.py
import json
from typing import Any
from uuid import UUID
from databases import Database
from .base import AsyncStorageBackend

class AsyncDatabaseBackend(AsyncStorageBackend):
    """Asynchronous database backend using `databases` library.

    Uses `databases` in native async mode for non-blocking operations.
    Supports PostgreSQL, MySQL, SQLite via unified interface.
    """

    def __init__(self, connection_string: str):
        """Initialize async backend.

        Args:
            connection_string: Database URL
                - postgresql://user:pass@localhost/db (uses asyncpg)
                - mysql://user:pass@localhost/db (uses aiomysql)
                - sqlite:///path/to/db.sqlite (uses aiosqlite)
        """
        self.connection_string = connection_string
        self.db = Database(connection_string)
        self._connected = False

    async def connect(self):
        """Connect to database (async)."""
        if not self._connected:
            await self.db.connect()
            self._connected = True

    async def disconnect(self):
        """Disconnect from database (async)."""
        if self._connected:
            await self.db.disconnect()
            self._connected = False

    async def create_job(
        self,
        id: UUID,
        queue: str,
        func_name: str,
        args: list[Any],
        kwargs: dict[str, Any],
        **options
    ) -> dict[str, Any]:
        """Create job using raw SQL INSERT (async)."""
        query = """
            INSERT INTO sqlery_jobs (
                id, queue, func_name, args, kwargs,
                status, priority, timeout, created_at, updated_at
            )
            VALUES (:id, :queue, :func_name, :args, :kwargs,
                    :status, :priority, :timeout, NOW(), NOW())
            RETURNING *
        """

        values = {
            "id": str(id),
            "queue": queue,
            "func_name": func_name,
            "args": json.dumps(args),
            "kwargs": json.dumps(kwargs),
            "status": "queued",
            "priority": options.get("priority", 0),
            "timeout": options.get("timeout"),
        }

        result = await self.db.fetch_one(query, values)
        return dict(result)

    async def claim_job(self, queue: str, worker_name: str) -> dict[str, Any] | None:
        """Atomically claim job using SELECT FOR UPDATE SKIP LOCKED (async)."""
        query = """
            UPDATE sqlery_jobs
            SET status = 'started',
                worker_name = :worker_name,
                started_at = NOW(),
                updated_at = NOW()
            WHERE id = (
                SELECT id FROM sqlery_jobs
                WHERE queue = :queue AND status = 'queued'
                ORDER BY priority DESC, created_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING *
        """

        values = {"worker_name": worker_name, "queue": queue}
        result = await self.db.fetch_one(query, values)
        return dict(result) if result else None

    async def update_job_status(self, job_id: UUID, status: str, **fields) -> None:
        """Update job status (async)."""
        # Build dynamic SET clause
        set_parts = ["status = :status", "updated_at = NOW()"]
        values = {"job_id": str(job_id), "status": status}

        for key, value in fields.items():
            set_parts.append(f"{key} = :{key}")
            values[key] = value

        query = f"""
            UPDATE sqlery_jobs
            SET {', '.join(set_parts)}
            WHERE id = :job_id
        """

        await self.db.execute(query, values)

    async def get_job(self, job_id: UUID) -> dict[str, Any] | None:
        """Retrieve job by ID (async)."""
        query = "SELECT * FROM sqlery_jobs WHERE id = :job_id"
        result = await self.db.fetch_one(query, {"job_id": str(job_id)})
        return dict(result) if result else None

    async def delete_job(self, job_id: UUID) -> None:
        """Delete job (async)."""
        query = "DELETE FROM sqlery_jobs WHERE id = :job_id"
        await self.db.execute(query, {"job_id": str(job_id)})

    async def list_jobs(
        self,
        queue: str | None = None,
        status: str | None = None,
        limit: int = 100
    ) -> list[dict[str, Any]]:
        """List jobs with filters (async)."""
        conditions = []
        values = {"limit": limit}

        if queue:
            conditions.append("queue = :queue")
            values["queue"] = queue

        if status:
            conditions.append("status = :status")
            values["status"] = status

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        query = f"""
            SELECT * FROM sqlery_jobs
            {where_clause}
            ORDER BY created_at DESC
            LIMIT :limit
        """

        results = await self.db.fetch_all(query, values)
        return [dict(row) for row in results]

    # ... implement all other abstract methods
```

### 4.4 Public API

```python
# src/sqlery/__init__.py
"""Sqlery: A standalone job queue library for Python."""

from .queue import Queue
from .worker import Worker
from .job import Job
from .decorators import job
from .scheduler import Scheduler
from .registry import (
    StartedRegistry,
    FinishedRegistry,
    FailedRegistry,
    ScheduledRegistry,
)

__all__ = [
    'Queue',
    'Worker',
    'Job',
    'job',
    'Scheduler',
    'StartedRegistry',
    'FinishedRegistry',
    'FailedRegistry',
    'ScheduledRegistry',
]

__version__ = '3.0.0'
```

### 4.5 Usage Examples

#### Synchronous Workflow (Default)

```python
from sqlery import Queue, Worker
from sqlery.backends.factory import BackendFactory

# Create sync backend
backend = BackendFactory.create_sync_backend('postgresql://localhost/myapp')
backend.connect()

# Initialize queue
queue = Queue(name='default', backend=backend)

# Enqueue a job
def send_email(to, subject, body):
    # Email sending logic (blocking)
    import smtplib
    # ... send email
    pass

job = queue.enqueue(send_email, 'user@example.com', 'Hello', 'Body')

# Run sync worker (processes jobs in subprocess)
worker = Worker([queue], backend=backend)
worker.work()  # Blocks until stopped
```

#### Asynchronous Workflow

```python
import asyncio
from sqlery import AsyncQueue, AsyncWorker
from sqlery.backends.factory import BackendFactory

async def main():
    # Create async backend
    backend = BackendFactory.create_async_backend('postgresql://localhost/myapp')
    await backend.connect()

    # Initialize async queue
    queue = AsyncQueue(name='default', backend=backend)

    # Enqueue a job (async)
    async def send_email_async(to, subject, body):
        # Async email sending logic
        import aiosmtplib
        # ... send email asynchronously
        pass

    job = await queue.enqueue(send_email_async, 'user@example.com', 'Hello', 'Body')

    # Run async worker
    worker = AsyncWorker([queue], backend=backend)
    await worker.work()  # Non-blocking, can be cancelled

    await backend.disconnect()

asyncio.run(main())
```

#### Decorator API (Sync)

```python
from sqlery import job, Queue
from sqlery.backends.factory import BackendFactory

# Configure default backend
backend = BackendFactory.create_sync_backend('postgresql://localhost/myapp')
backend.connect()

Queue.configure(backend=backend)

@job(queue='default', timeout=300)
def process_video(video_id):
    # Process video (blocking)
    import subprocess
    subprocess.run(['ffmpeg', '-i', f'video_{video_id}.mp4', ...])

# Enqueue job
job = process_video.delay(video_id=123)

# Check status (blocking)
print(job.status)  # 'queued'
print(job.result)  # None (not finished yet)
```

#### Decorator API (Async)

```python
import asyncio
from sqlery import async_job, AsyncQueue
from sqlery.backends.factory import BackendFactory

async def main():
    # Configure async backend
    backend = BackendFactory.create_async_backend('postgresql://localhost/myapp')
    await backend.connect()

    AsyncQueue.configure(backend=backend)

    @async_job(queue='default', timeout=300)
    async def process_video_async(video_id):
        # Process video (async)
        import asyncio
        proc = await asyncio.create_subprocess_exec(
            'ffmpeg', '-i', f'video_{video_id}.mp4', ...
        )
        await proc.wait()

    # Enqueue job (async)
    job = await process_video_async.delay(video_id=123)

    # Check status (async)
    status = await job.get_status()
    print(status)  # 'queued'

asyncio.run(main())
```

#### Scheduling (Sync)

```python
from sqlery import Queue
from sqlery.backends.factory import BackendFactory

backend = BackendFactory.create_sync_backend('postgresql://localhost/myapp')
backend.connect()

queue = Queue(backend=backend)

# Schedule a job to run at specific time
from datetime import datetime, timedelta
run_at = datetime.now() + timedelta(hours=1)
queue.enqueue_at(run_at, send_report, 'admin@example.com')

# Schedule recurring job (cron syntax)
queue.schedule(
    cron='0 2 * * *',  # Daily at 2 AM
    func=cleanup_old_data,
    queue='maintenance'
)
```

#### Scheduling (Async)

```python
import asyncio
from sqlery import AsyncQueue
from sqlery.backends.factory import BackendFactory

async def main():
    backend = BackendFactory.create_async_backend('postgresql://localhost/myapp')
    await backend.connect()

    queue = AsyncQueue(backend=backend)

    # Schedule a job to run at specific time (async)
    from datetime import datetime, timedelta
    run_at = datetime.now() + timedelta(hours=1)
    await queue.enqueue_at(run_at, send_report, 'admin@example.com')

    # Schedule recurring job (async)
    await queue.schedule(
        cron='0 2 * * *',  # Daily at 2 AM
        func=cleanup_old_data,
        queue='maintenance'
    )

asyncio.run(main())
```

---

## 5. Factory Pattern and Sync/Async Architecture

### 5.1 Design Philosophy

Sqlery v3.0 supports **both synchronous and asynchronous workflows** using a Factory Pattern:

- **Sync Backend**: For CLI tools, Django integration, and traditional blocking workflows
- **Async Backend**: For FastAPI, async workers, and high-concurrency applications
- **Single Database Library**: Uses `databases` library which provides unified API for both modes
- **Factory Pattern**: Centralizes backend creation logic and simplifies switching between modes

### 5.2 Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    Application Layer                         │
│  (CLI, Django views, FastAPI endpoints, User code)          │
└────────────────────┬────────────────────┬───────────────────┘
                     │                    │
         ┌───────────▼──────────┐  ┌─────▼──────────────┐
         │   Sync Workflow      │  │  Async Workflow    │
         │                      │  │                    │
         │  - Queue             │  │  - AsyncQueue      │
         │  - Worker            │  │  - AsyncWorker     │
         │  - Job               │  │  - AsyncJob        │
         │  - Scheduler         │  │  - AsyncScheduler  │
         └──────────┬───────────┘  └─────┬──────────────┘
                    │                    │
         ┌──────────▼────────────────────▼──────────────┐
         │         BackendFactory                       │
         │  create_backend(type='sync'|'async')         │
         └──────────┬────────────────────┬──────────────┘
                    │                    │
      ┌─────────────▼──────────┐  ┌─────▼─────────────────┐
      │  SyncDatabaseBackend   │  │ AsyncDatabaseBackend  │
      │  (SyncStorageBackend)  │  │ (AsyncStorageBackend) │
      └─────────────┬──────────┘  └─────┬─────────────────┘
                    │                    │
                    │    ┌───────────────▼──────────────┐
                    └────►     databases library        │
                         │  (unified database interface)│
                         └───────────────┬──────────────┘
                                         │
                    ┌────────────────────┼────────────────────┐
                    │                    │                    │
         ┌──────────▼──────┐  ┌─────────▼────────┐  ┌───────▼────────┐
         │  PostgreSQL     │  │     MySQL        │  │    SQLite      │
         │  (asyncpg/      │  │  (aiomysql/      │  │  (aiosqlite/   │
         │   psycopg)      │  │   pymysql)       │  │   sqlite3)     │
         └─────────────────┘  └──────────────────┘  └────────────────┘
```

### 5.3 When to Use Sync vs Async

#### Use Synchronous Backend When:

✅ Building CLI tools (sqlery commands)
✅ Integrating with Django (Django ORM is sync)
✅ Simple scripts and batch jobs
✅ Workers running in separate processes (default)
✅ Traditional blocking I/O is acceptable
✅ Simpler code without async/await complexity

**Example Use Cases**:
- Command-line worker: `sqlery worker --queue default`
- Django management command: `python manage.py run_jobs`
- Simple background job processor
- Scheduled tasks running via cron

#### Use Asynchronous Backend When:

✅ Building async web applications (FastAPI, Starlette)
✅ High-concurrency job processing
✅ Real-time job status updates via WebSockets
✅ Integrating with async libraries (aiohttp, asyncpg, etc.)
✅ Microservices with async communication
✅ Event-driven architectures

**Example Use Cases**:
- FastAPI endpoints enqueueing jobs
- Async worker processing thousands of jobs/second
- WebSocket dashboard for real-time job monitoring
- Async job chains and workflows

### 5.4 Backend Factory Implementation Details

#### Factory Pattern Benefits

1. **Centralized Creation**: Single point for backend instantiation
2. **Type Safety**: Returns properly typed backend instances
3. **Flexibility**: Easy to add new backend types (Redis, etc.)
4. **Testing**: Simplified mocking and testing
5. **Configuration**: Can load from config files
6. **Auto-Detection**: Automatically detects async context and creates appropriate backend

#### Extended Factory with Configuration

```python
# src/sqlery/backends/factory.py
from typing import Literal, Any
from pathlib import Path
import yaml
from .base import SyncStorageBackend, AsyncStorageBackend
from .sync_backend import SyncDatabaseBackend
from .async_backend import AsyncDatabaseBackend

BackendType = Literal['sync', 'async']

class BackendFactory:
    """Factory for creating storage backends."""

    _default_backend: SyncStorageBackend | AsyncStorageBackend | None = None

    @classmethod
    def create_backend(
        cls,
        connection_string: str,
        backend_type: BackendType | None = None,
        **options: Any
    ) -> SyncStorageBackend | AsyncStorageBackend:
        """Create a backend instance.

        Args:
            connection_string: Database connection URL
            backend_type: 'sync', 'async', or None (auto-detect from context)
            **options: Additional backend-specific options
                - pool_size: Connection pool size (default: 5)
                - pool_timeout: Connection timeout (default: 30)
                - pool_max_overflow: Max overflow connections (default: 10)

        Returns:
            Backend instance

        Note:
            If backend_type is None, automatically detects the context:
            - Inside async event loop → creates async backend
            - Outside async event loop → creates sync backend
        """
        # Auto-detect backend type if not specified
        if backend_type is None:
            backend_type = cls._detect_backend_type()

        if backend_type == 'sync':
            return SyncDatabaseBackend(connection_string, **options)
        elif backend_type == 'async':
            return AsyncDatabaseBackend(connection_string, **options)
        else:
            raise ValueError(f"Invalid backend_type: {backend_type}")

    @staticmethod
    def _detect_backend_type() -> BackendType:
        """Detect whether to use sync or async backend based on context.

        Returns:
            'async' if running inside async event loop, 'sync' otherwise

        Detection logic:
            1. Check if asyncio event loop is running
            2. If running → async backend
            3. If not running → sync backend
        """
        import asyncio
        try:
            asyncio.get_running_loop()
            return 'async'
        except RuntimeError:
            # No event loop running
            return 'sync'

    @classmethod
    def create_from_config(
        cls,
        config_path: str | Path
    ) -> SyncStorageBackend | AsyncStorageBackend:
        """Create backend from configuration file.

        Args:
            config_path: Path to YAML config file

        Returns:
            Backend instance

        Example config (sqlery.yml):
            connection: postgresql://localhost/myapp
            backend_type: sync
            pool_size: 10
            pool_timeout: 30
        """
        config_path = Path(config_path)
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(config_path) as f:
            config = yaml.safe_load(f)

        return cls.create_backend(
            connection_string=config['connection'],
            backend_type=config.get('backend_type', 'sync'),
            pool_size=config.get('pool_size', 5),
            pool_timeout=config.get('pool_timeout', 30),
        )

    @classmethod
    def set_default_backend(cls, backend: SyncStorageBackend | AsyncStorageBackend):
        """Set default backend for the application."""
        cls._default_backend = backend

    @classmethod
    def get_default_backend(cls) -> SyncStorageBackend | AsyncStorageBackend:
        """Get default backend."""
        if cls._default_backend is None:
            raise RuntimeError("No default backend configured. Call set_default_backend() first.")
        return cls._default_backend
```

#### Usage with Auto-Detection

```python
# Automatic detection based on context

# Example 1: Sync context (no event loop)
from sqlery.backends.factory import BackendFactory

# Auto-detects sync context → creates SyncDatabaseBackend
backend = BackendFactory.create_backend('postgresql://localhost/myapp')
print(type(backend).__name__)  # SyncDatabaseBackend
backend.connect()

# Example 2: Async context (inside event loop)
import asyncio

async def main():
    # Auto-detects async context → creates AsyncDatabaseBackend
    backend = BackendFactory.create_backend('postgresql://localhost/myapp')
    print(type(backend).__name__)  # AsyncDatabaseBackend
    await backend.connect()

asyncio.run(main())

# Example 3: Explicit type (override auto-detection)
# Force sync backend even in async context
backend = BackendFactory.create_backend(
    'postgresql://localhost/myapp',
    backend_type='sync'
)
```

#### Usage with Configuration File

```python
# app.py
from sqlery.backends.factory import BackendFactory

# Load backend from config file (auto-detects if backend_type not specified)
backend = BackendFactory.create_from_config('sqlery.yml')
backend.connect()  # or await backend.connect() if async

# Set as default for entire application
BackendFactory.set_default_backend(backend)

# Now all Queue/Worker instances can use default backend
from sqlery import Queue, Worker

queue = Queue(name='default')  # Uses default backend
worker = Worker([queue])  # Uses default backend
worker.work()
```

### 5.5 Dual Queue/Worker Classes

Sqlery provides both sync and async versions of core classes:

```python
# src/sqlery/__init__.py

# Synchronous API (default)
from .queue import Queue
from .worker import Worker
from .job import Job
from .scheduler import Scheduler

# Asynchronous API
from .async_queue import AsyncQueue
from .async_worker import AsyncWorker
from .async_job import AsyncJob
from .async_scheduler import AsyncScheduler

# Decorators (detect sync vs async functions)
from .decorators import job, async_job

__all__ = [
    # Sync API
    'Queue',
    'Worker',
    'Job',
    'Scheduler',
    # Async API
    'AsyncQueue',
    'AsyncWorker',
    'AsyncJob',
    'AsyncScheduler',
    # Decorators
    'job',
    'async_job',
]
```

### 5.6 Smart Decorator (Auto-detect Sync/Async)

```python
# src/sqlery/decorators.py
import asyncio
import inspect
from functools import wraps
from typing import Callable, Any

def job(queue: str = 'default', **options):
    """Decorator for both sync and async job functions.

    Automatically detects if function is async and uses appropriate backend.

    Args:
        queue: Queue name
        **options: timeout, priority, retry, etc.

    Examples:
        @job(queue='default', timeout=300)
        def sync_task(x, y):
            return x + y

        @job(queue='default', timeout=300)
        async def async_task(x, y):
            await asyncio.sleep(1)
            return x + y
    """
    def decorator(func: Callable) -> Callable:
        is_async = asyncio.iscoroutinefunction(func)

        if is_async:
            # Async function
            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                return await func(*args, **kwargs)

            async def async_delay(*args, **kwargs):
                from sqlery import AsyncQueue
                from sqlery.backends.factory import BackendFactory

                backend = BackendFactory.get_default_backend()
                queue_obj = AsyncQueue(name=queue, backend=backend)
                return await queue_obj.enqueue(func, *args, **kwargs)

            async_wrapper.delay = async_delay
            async_wrapper.queue = queue
            async_wrapper.options = options
            return async_wrapper
        else:
            # Sync function
            @wraps(func)
            def sync_wrapper(*args, **kwargs):
                return func(*args, **kwargs)

            def sync_delay(*args, **kwargs):
                from sqlery import Queue
                from sqlery.backends.factory import BackendFactory

                backend = BackendFactory.get_default_backend()
                queue_obj = Queue(name=queue, backend=backend)
                return queue_obj.enqueue(func, *args, **kwargs)

            sync_wrapper.delay = sync_delay
            sync_wrapper.queue = queue
            sync_wrapper.options = options
            return sync_wrapper

    return decorator


# Explicit decorators for clarity
def sync_job(queue: str = 'default', **options):
    """Decorator explicitly for sync functions."""
    def decorator(func: Callable) -> Callable:
        if asyncio.iscoroutinefunction(func):
            raise TypeError(f"sync_job cannot decorate async function: {func.__name__}")
        return job(queue, **options)(func)
    return decorator


def async_job(queue: str = 'default', **options):
    """Decorator explicitly for async functions."""
    def decorator(func: Callable) -> Callable:
        if not asyncio.iscoroutinefunction(func):
            raise TypeError(f"async_job cannot decorate sync function: {func.__name__}")
        return job(queue, **options)(func)
    return decorator
```

### 5.7 Testing Strategy for Sync/Async

```python
# tests/backends/test_backend_factory.py
import pytest
from sqlery.backends.factory import BackendFactory
from sqlery.backends.base import SyncStorageBackend, AsyncStorageBackend

def test_create_sync_backend():
    """Test creating sync backend."""
    backend = BackendFactory.create_backend(
        'postgresql://localhost/test',
        backend_type='sync'
    )
    assert isinstance(backend, SyncStorageBackend)

def test_create_async_backend():
    """Test creating async backend."""
    backend = BackendFactory.create_backend(
        'postgresql://localhost/test',
        backend_type='async'
    )
    assert isinstance(backend, AsyncStorageBackend)

def test_invalid_backend_type():
    """Test invalid backend type raises error."""
    with pytest.raises(ValueError, match="Invalid backend_type"):
        BackendFactory.create_backend(
            'postgresql://localhost/test',
            backend_type='invalid'
        )

def test_create_from_config(tmp_path):
    """Test creating backend from config file."""
    config_file = tmp_path / "sqlery.yml"
    config_file.write_text("""
connection: postgresql://localhost/test
backend_type: sync
pool_size: 10
    """)

    backend = BackendFactory.create_from_config(config_file)
    assert isinstance(backend, SyncStorageBackend)
```

---

## 6. File Reorganization

### 6.1 Phase 1: Create New Structure

**Create standalone backends**:
```
src/sqlery/backends/
├── __init__.py
├── base.py           # Abstract base classes (SyncStorageBackend, AsyncStorageBackend)
├── factory.py        # BackendFactory for creating backends
├── sync_backend.py   # Synchronous backend using `databases`
└── async_backend.py  # Asynchronous backend using `databases`
```

**Create core modules** (both sync and async versions):
```
src/sqlery/
├── __init__.py       # Public API exports
│
├── queue.py          # Sync Queue class
├── async_queue.py    # Async Queue class
│
├── worker.py         # Sync Worker class
├── async_worker.py   # Async Worker class
│
├── job.py            # Sync Job class
├── async_job.py      # Async Job class
│
├── scheduler.py      # Sync Scheduler class
├── async_scheduler.py # Async Scheduler class
│
├── decorators.py     # Smart @job decorator (detects sync/async)
├── daemon.py         # Daemon manager (sync)
├── registry.py       # Registry classes (sync/async)
├── cleanup.py        # Cleanup utilities (sync/async)
└── cli.py            # Core CLI (uses sync backend by default)
```

**Create migrations directory**:
```
src/sqlery/migrations/
├── __init__.py
├── runner.py         # Migration runner
├── 001_initial.sql
├── 002_add_cron_jobs.sql
└── ...
```

### 6.2 Phase 2: Move Django Integration

**Move Django files to integration**:
```
src/sqlery/integrations/django/
├── __init__.py       # Django integration entry point
├── backend.py        # DjangoBackend adapter
├── models.py         # Django models (from sqlery/models.py)
├── admin.py          # Django admin (from sqlery/admin.py)
├── middleware.py     # Django middleware (from sqlery/middleware.py)
├── apps.py           # App config (from sqlery/apps.py)
├── config.py         # Django settings (from sqlery/django/config.py)
├── management/       # Django commands (from sqlery/management/)
│   └── commands/
│       ├── run_jobs.py
│       ├── daemon.py
│       └── cleanup_jobs.py
└── migrations/       # Generated from core SQL
    ├── 0001_initial.py
    └── ...
```

**Update Django backend to wrap core**:

```python
# src/sqlery/integrations/django/backend.py
from sqlery.backends.base import StorageBackend
from .models import Job as DjangoJob

class DjangoBackend(StorageBackend):
    """Adapter that wraps Django ORM to implement StorageBackend."""

    def create_job(self, id, queue, func_name, args, kwargs, **options):
        job = DjangoJob.objects.create(
            id=id,
            queue=queue,
            func_name=func_name,
            args=args,
            kwargs=kwargs,
            status='queued',
            **options
        )
        return self._model_to_dict(job)

    # ... implement all backend methods using Django ORM
```

### 6.3 Phase 3: Rename FastAPI → Standalone

**Rename and reorganize**:
```
src/sqlery/integrations/fastapi/
├── __init__.py       # FastAPI integration
├── app.py            # FastAPI web UI (from sqlery/fastapi/app.py)
├── routes.py         # API routes
└── templates/        # Jinja2 templates
```

**Note**: Core sqlery doesn't depend on FastAPI. The web UI is optional.

### 6.4 Phase 4: Update Imports

**Update all imports across codebase**:
- `from sqlery.core.worker import Worker` → `from sqlery import Worker`
- `from sqlery.models import Job` → `from sqlery.integrations.django.models import Job`
- `from sqlery.fastapi.backend import SQLAlchemyBackend` → `from sqlery.backends.postgres import PostgresBackend`

### 6.5 Phase 5: Remove Legacy

**Delete obsolete files**:
```
✗ src/sqlery/core/           # Merged into root
✗ src/sqlery/django_sqlery/         # Moved to integrations/
✗ src/sqlery/fastapi/        # Moved to integrations/
✗ src/sqlery/compat.py       # Split into backends/base.py
✗ alembic/                   # Replaced with raw SQL migrations
✗ alembic.ini                # Not needed
```

---

## 6. Django Integration Plugin

### 6.1 Design Philosophy

Django integration should:
- Import from core `sqlery` package
- Provide Django-specific conveniences (models, admin, middleware)
- Use `DjangoBackend` adapter to wrap Django ORM
- Be completely optional (core sqlery works without Django)

### 6.2 Installation

```bash
# Install core sqlery
pip install sqlery

# Install Django integration
pip install sqlery[django]
# or
pip install sqlery-django  # Separate package (future)
```

### 6.3 Django Settings

```python
# settings.py
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    # ...
    'sqlery.integrations.django',  # Add Django integration
]

SQLERY = {
    'backend': 'sqlery.integrations.django.backend.DjangoBackend',
    'queues': ['default', 'high-priority', 'low-priority'],
    'worker_count': 4,
}
```

### 6.4 Django Models

```python
# sqlery/integrations/django/models.py
from django.db import models
from django.contrib.postgres.fields import JSONField

class Job(models.Model):
    """Django model for sqlery jobs.

    This is a Django ORM wrapper around the core sqlery schema.
    The core schema is defined in raw SQL migrations.
    """

    id = models.UUIDField(primary_key=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    queue = models.CharField(max_length=255, db_index=True)
    func_name = models.CharField(max_length=512)
    args = JSONField(default=list)
    kwargs = JSONField(default=dict)

    status = models.CharField(max_length=20, db_index=True)
    priority = models.IntegerField(default=0)
    timeout = models.IntegerField(null=True, blank=True)

    result = JSONField(null=True, blank=True)
    error = JSONField(null=True, blank=True)

    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    worker_name = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        db_table = 'sqlery_jobs'  # Use same table as core sqlery
        indexes = [
            models.Index(fields=['queue', 'status']),
            models.Index(fields=['created_at']),
        ]
```

### 6.5 Django Management Commands

```python
# sqlery/integrations/django/management/commands/run_jobs.py
from django.core.management.base import BaseCommand
from sqlery import Worker, Queue
from sqlery.integrations.django.backend import DjangoBackend

class Command(BaseCommand):
    help = "Run sqlery worker using Django ORM backend"

    def add_arguments(self, parser):
        parser.add_argument('--queue', default='default')
        parser.add_argument('--burst', action='store_true')

    def handle(self, *args, **options):
        backend = DjangoBackend()
        queue = Queue(name=options['queue'], backend=backend)
        worker = Worker([queue], backend=backend)

        worker.work(burst=options['burst'])
```

### 6.6 Django Admin

```python
# sqlery/integrations/django/admin.py
from django.contrib import admin
from .models import Job, CronJob

@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ['id', 'func_name', 'queue', 'status', 'created_at']
    list_filter = ['status', 'queue', 'created_at']
    search_fields = ['func_name', 'id']
    readonly_fields = ['id', 'created_at', 'updated_at']

    actions = ['cancel_jobs', 'retry_jobs']

    def cancel_jobs(self, request, queryset):
        count = queryset.filter(status='queued').update(status='canceled')
        self.message_user(request, f"Canceled {count} jobs")
```

### 6.7 Django Usage Example

```python
# myapp/tasks.py
from sqlery import job

@job(queue='default')
def process_order(order_id):
    from myapp.models import Order
    order = Order.objects.get(id=order_id)
    # Process order logic
    return f"Processed order {order_id}"

# myapp/views.py
from django.http import JsonResponse
from .tasks import process_order

def create_order(request):
    order = Order.objects.create(...)

    # Enqueue background job
    job = process_order.delay(order.id)

    return JsonResponse({
        'order_id': order.id,
        'job_id': str(job.id),
    })
```

---

## 7. FastAPI Integration Plugin

### 7.1 Design Philosophy

FastAPI integration should:
- Import from core `sqlery` package
- Provide web UI dashboard
- Use core `PostgresBackend` or `SQLiteBackend`
- Be completely optional

### 7.2 Installation

```bash
# Install core sqlery
pip install sqlery

# Install FastAPI web UI
pip install sqlery[fastapi]
```

### 7.3 FastAPI App

```python
# sqlery/integrations/fastapi/app.py
from fastapi import FastAPI
from sqlery import Queue
from sqlery.backends.postgres import PostgresBackend

def create_app(connection_string: str) -> FastAPI:
    """Create FastAPI app with sqlery web UI."""

    app = FastAPI(title="Sqlery Dashboard")
    backend = PostgresBackend(connection_string)
    backend.connect()

    # Job list endpoint
    @app.get("/api/jobs")
    def list_jobs(queue: str = None, status: str = None, limit: int = 100):
        jobs = backend.list_jobs(
            queue=queue,
            status=status,
            limit=limit
        )
        return {"jobs": jobs}

    # Job detail endpoint
    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = backend.get_job(job_id)
        return {"job": job}

    # Cancel job endpoint
    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        backend.update_job_status(job_id, 'canceled')
        return {"status": "canceled"}

    # Dashboard UI (serve HTML)
    @app.get("/")
    def dashboard():
        # Render Jinja2 template
        pass

    return app
```

### 7.4 FastAPI Usage Example

```python
# main.py
from fastapi import FastAPI
from sqlery import Queue, job

# Initialize sqlery
Queue.configure(connection='postgresql://localhost/myapp')

@job(queue='default')
def process_data(data_id):
    # Process data logic
    pass

# Create FastAPI app
app = FastAPI()

@app.post("/process")
def enqueue_processing(data_id: int):
    job = process_data.delay(data_id)
    return {"job_id": str(job.id)}

# Add sqlery web UI
from sqlery.integrations.fastapi import create_app as create_sqlery_app
sqlery_app = create_sqlery_app(connection='postgresql://localhost/myapp')
app.mount("/jobs", sqlery_app)
```

---

## 8. CLI Strategy

### 8.1 Core CLI Commands

```bash
# Worker management
sqlery worker --queue default           # Run single worker
sqlery worker --queue default --burst   # Run until queue empty

# Daemon management (multi-worker)
sqlery daemon start --workers 4         # Start daemon with 4 workers
sqlery daemon stop                      # Stop daemon
sqlery daemon status                    # Check daemon status

# Scheduler
sqlery scheduler                        # Run cron scheduler

# Job management
sqlery jobs list --queue default        # List jobs
sqlery jobs cancel <job_id>             # Cancel a job
sqlery jobs retry <job_id>              # Retry a failed job
sqlery jobs clear --queue default       # Clear queue

# Cleanup
sqlery cleanup --older-than 7d          # Delete jobs older than 7 days
sqlery cleanup --keep-last 1000         # Keep only last 1000 jobs

# Database management
sqlery migrate                          # Run migrations
sqlery migrate --status                 # Check migration status
sqlery migrate --create "add_field"     # Create new migration

# Information
sqlery info                             # Show queue stats
sqlery queues                           # List all queues
sqlery workers                          # List active workers
```

### 8.2 Django Commands

Django users can use either:

**Core CLI** (recommended):
```bash
sqlery worker --queue default
```

**Django management commands** (for convenience):
```bash
python manage.py run_jobs --queue default
python manage.py daemon start --workers 4
python manage.py cleanup_jobs --older-than 7d
```

### 8.3 Configuration File

Support configuration file for CLI:

```yaml
# sqlery.yml
connection: postgresql://localhost/myapp

queues:
  - default
  - high-priority
  - low-priority

worker:
  count: 4
  burst: false
  log_level: info

scheduler:
  enabled: true
  interval: 60

cleanup:
  older_than: 7d
  keep_last: 10000
  run_interval: 86400
```

Load config:

```bash
sqlery --config sqlery.yml daemon start
```

---

## 9. Implementation Phases

### Phase 1: Foundation (Week 1-2)

**Goal**: Create standalone core without breaking existing functionality

**Tasks**:
1. ✅ Create `src/sqlery/backends/` directory
2. ✅ Extract `StorageBackend` abstract class to `backends/base.py`
3. ✅ Implement `PostgresBackend` with raw SQL (no SQLAlchemy)
4. ✅ Implement `SQLiteBackend` with raw SQL
5. ✅ Create raw SQL migration files in `src/sqlery/migrations/`
6. ✅ Implement `MigrationRunner` for applying SQL migrations
7. ✅ Update core modules to use new backends
8. ✅ Add tests for backends and migrations

**Success Criteria**:
- Core sqlery works with `PostgresBackend` (no ORM)
- Migrations run successfully with raw SQL
- All existing tests pass

### Phase 2: Reorganization (Week 3-4)

**Goal**: Move Django code to integration layer

**Tasks**:
1. ✅ Create `src/sqlery/integrations/django/` directory
2. ✅ Move Django models, admin, middleware to integration
3. ✅ Create `DjangoBackend` adapter that wraps Django ORM
4. ✅ Move Django management commands to integration
5. ✅ Update imports across Django integration code
6. ✅ Update Django tests to use new import paths
7. ✅ Create Django integration documentation

**Success Criteria**:
- Django integration works via `sqlery.integrations.django`
- Django tests pass
- No Django imports in core `sqlery/` modules

### Phase 3: API Cleanup (Week 5)

**Goal**: Simplify public API and remove deprecated code

**Tasks**:
1. ✅ Define clean public API in `src/sqlery/__init__.py`
2. ✅ Move core classes to root: `Queue`, `Worker`, `Job`, etc.
3. ✅ Remove `sqlery/core/` directory (merge into root)
4. ✅ Rename `sqlery/fastapi/` → `sqlery/integrations/fastapi/`
5. ✅ Update all internal imports
6. ✅ Remove `compat.py` (replaced by backends)
7. ✅ Update CLI to use new structure

**Success Criteria**:
- Public API is clean: `from sqlery import Queue, Worker, job`
- No `core` subdirectory
- All tests pass with new imports

### Phase 4: Dependency Cleanup (Week 6)

**Goal**: Remove ORM dependencies from core

**Tasks**:
1. ✅ Update `pyproject.toml` to remove SQLAlchemy/Alembic from core
2. ✅ Make Django optional: `sqlery[django]`
3. ✅ Make FastAPI optional: `sqlery[fastapi]`
4. ✅ Verify core sqlery installs with minimal dependencies
5. ✅ Update CI to test core installation separately
6. ✅ Update documentation to reflect optional dependencies

**Success Criteria**:
- `pip install sqlery` has ~5 dependencies (no ORMs)
- `pip install sqlery[django]` adds Django
- `pip install sqlery[fastapi]` adds FastAPI
- Core tests run without Django/FastAPI installed

### Phase 5: Documentation (Week 7)

**Goal**: Update docs to reflect standalone-first design

**Tasks**:
1. ✅ Rewrite README to show standalone usage first
2. ✅ Create standalone quickstart guide
3. ✅ Document PostgreSQL backend usage
4. ✅ Document SQLite backend usage
5. ✅ Create Django integration guide
6. ✅ Create FastAPI integration guide
7. ✅ Document migration system
8. ✅ Update CLI reference
9. ✅ Create comparison with RQ, Celery, etc.

**Success Criteria**:
- README shows standalone usage prominently
- Django/FastAPI are presented as integrations
- All features documented with examples

### Phase 6: Testing & Polish (Week 8)

**Goal**: Ensure reliability and polish release

**Tasks**:
1. ✅ Add comprehensive tests for standalone mode
2. ✅ Add tests for PostgreSQL backend
3. ✅ Add tests for SQLite backend
4. ✅ Add integration tests for Django
5. ✅ Add integration tests for FastAPI
6. ✅ Performance benchmarks (compare with RQ, Celery)
7. ✅ Security audit (SQL injection, etc.)
8. ✅ Example projects demonstrating common use cases
9. ✅ Load testing under realistic workloads
10. ✅ Release notes and changelog

**Success Criteria**:
- 90%+ test coverage
- All CI checks pass
- Example projects run successfully
- Performance targets met (1000+ jobs/second per worker)

### Phase 7: Release (Week 9)

**Goal**: Ship v3.0 with standalone architecture

**Tasks**:
1. ✅ Version bump to 3.0.0
2. ✅ Tag release in git
3. ✅ Publish to PyPI
4. ✅ Update documentation site
5. ✅ Announce on social media, mailing lists
6. ✅ Monitor for issues and feedback

---

## 10. Testing Strategy

### 10.1 Test Structure

```
tests/
├── core/                          # Core sqlery tests (no Django)
│   ├── test_queue.py
│   ├── test_worker.py
│   ├── test_job.py
│   ├── test_scheduler.py
│   ├── test_decorators.py
│   └── test_registry.py
│
├── backends/                      # Backend tests
│   ├── test_postgres_backend.py   # Test raw SQL backend
│   ├── test_sqlite_backend.py     # Test SQLite backend
│   └── test_backend_interface.py  # Abstract interface tests
│
├── migrations/                    # Migration tests
│   ├── test_migration_runner.py
│   └── test_sql_migrations.py
│
├── integrations/                  # Integration tests
│   ├── django/
│   │   ├── test_django_backend.py
│   │   ├── test_django_models.py
│   │   ├── test_management_commands.py
│   │   └── conftest.py
│   │
│   └── fastapi/
│       ├── test_fastapi_app.py
│       └── conftest.py
│
├── cli/                           # CLI tests
│   ├── test_cli_commands.py
│   └── test_cli_config.py
│
└── conftest.py                    # Shared fixtures
```

### 10.2 Core Tests (No Django)

```python
# tests/core/test_queue.py
import pytest
from sqlery import Queue
from sqlery.backends.postgres import PostgresBackend

@pytest.fixture
def backend():
    """Provide PostgreSQL backend for testing."""
    backend = PostgresBackend('postgresql://localhost/sqlery_test')
    backend.connect()
    # Run migrations
    yield backend
    # Cleanup

def test_enqueue_job(backend):
    """Test enqueueing a job without Django."""
    queue = Queue(name='default', backend=backend)

    def sample_task(x, y):
        return x + y

    job = queue.enqueue(sample_task, 1, 2)

    assert job.id is not None
    assert job.func_name == 'sample_task'
    assert job.args == [1, 2]
    assert job.status == 'queued'

def test_worker_executes_job(backend):
    """Test worker execution without Django."""
    queue = Queue(name='default', backend=backend)
    worker = Worker([queue], backend=backend)

    # Enqueue job
    job = queue.enqueue(lambda: 42)

    # Execute job
    worker.work(burst=True)

    # Verify result
    job.refresh()
    assert job.status == 'finished'
    assert job.result == 42
```

### 10.3 Backend Tests

```python
# tests/backends/test_postgres_backend.py
import pytest
from sqlery.backends.postgres import PostgresBackend
from uuid import uuid4

@pytest.fixture
def backend():
    backend = PostgresBackend('postgresql://localhost/sqlery_test')
    backend.connect()
    # Clear tables
    backend.execute("DELETE FROM sqlery_jobs")
    yield backend
    backend.close()

def test_create_job(backend):
    """Test raw SQL job creation."""
    job_id = uuid4()
    job = backend.create_job(
        id=job_id,
        queue='default',
        func_name='test_func',
        args=[1, 2],
        kwargs={'x': 3}
    )

    assert job['id'] == str(job_id)
    assert job['queue'] == 'default'
    assert job['status'] == 'queued'

def test_claim_job_atomic(backend):
    """Test atomic job claiming (no race conditions)."""
    # Create multiple jobs
    for i in range(10):
        backend.create_job(
            id=uuid4(),
            queue='default',
            func_name='test',
            args=[i],
            kwargs={}
        )

    # Claim job from multiple workers (simulated)
    claimed_jobs = []
    for worker_id in range(5):
        job = backend.claim_job('default', f'worker-{worker_id}')
        if job:
            claimed_jobs.append(job)

    # Verify no duplicate claims
    job_ids = [j['id'] for j in claimed_jobs]
    assert len(job_ids) == len(set(job_ids))  # All unique
```

### 10.4 Migration Tests

```python
# tests/migrations/test_migration_runner.py
import pytest
from sqlery.migrations.runner import MigrationRunner
from sqlery.backends.postgres import PostgresBackend

def test_run_migrations(backend):
    """Test running SQL migrations."""
    runner = MigrationRunner(backend)

    # Check pending migrations
    pending = runner.get_pending_migrations()
    assert len(pending) > 0

    # Apply migrations
    applied = runner.migrate()

    # Verify migrations applied
    assert len(applied) == len(pending)

    # Verify tables exist
    tables = backend.fetchall("""
        SELECT table_name FROM information_schema.tables
        WHERE table_name LIKE 'sqlery_%'
    """)
    assert 'sqlery_jobs' in [t[0] for t in tables]
    assert 'sqlery_cron_jobs' in [t[0] for t in tables]

def test_migration_idempotency(backend):
    """Test migrations can be re-run safely."""
    runner = MigrationRunner(backend)

    # Run migrations twice
    runner.migrate()
    applied = runner.migrate()

    # Second run should apply nothing
    assert len(applied) == 0
```

### 10.5 Django Integration Tests

```python
# tests/integrations/django/test_django_backend.py
import pytest
import django
from django.conf import settings

# Configure Django for testing
if not settings.configured:
    settings.configure(
        DATABASES={
            'default': {
                'ENGINE': 'django.db.backends.postgresql',
                'NAME': 'sqlery_test',
            }
        },
        INSTALLED_APPS=[
            'django.contrib.contenttypes',
            'sqlery.integrations.django',
        ],
    )
    django.setup()

from sqlery import Queue, Worker
from sqlery.integrations.django.backend import DjangoBackend
from sqlery.integrations.django.models import Job as DjangoJob

def test_django_backend_enqueue():
    """Test enqueueing job with Django ORM."""
    backend = DjangoBackend()
    queue = Queue(name='default', backend=backend)

    job = queue.enqueue(lambda: 42)

    # Verify job in Django ORM
    django_job = DjangoJob.objects.get(id=job.id)
    assert django_job.status == 'queued'
    assert django_job.func_name == '<lambda>'

def test_django_management_command():
    """Test Django management command."""
    from django.core.management import call_command
    from io import StringIO

    out = StringIO()
    call_command('run_jobs', '--burst', stdout=out)

    # Verify command ran
    assert 'Worker started' in out.getvalue()
```

### 10.6 CI Configuration

```yaml
# .github/workflows/test.yml
name: Tests

on: [push, pull_request]

jobs:
  test-core:
    name: Test Core (No Django)
    runs-on: ubuntu-latest

    services:
      postgres:
        image: postgres:15
        env:
          POSTGRES_DB: sqlery_test
          POSTGRES_PASSWORD: postgres

    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.13'

      - name: Install core dependencies only
        run: pip install -e .  # No Django, no FastAPI

      - name: Run core tests
        run: pytest tests/core tests/backends tests/migrations

  test-django:
    name: Test Django Integration
    runs-on: ubuntu-latest

    services:
      postgres:
        image: postgres:15

    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4

      - name: Install with Django
        run: pip install -e ".[django]"

      - name: Run Django tests
        run: pytest tests/integrations/django

  test-fastapi:
    name: Test FastAPI Integration
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4

      - name: Install with FastAPI
        run: pip install -e ".[fastapi]"

      - name: Run FastAPI tests
        run: pytest tests/integrations/fastapi
```

---

## 11. V3.0 API Design (No Legacy Support)

**Note**: Since v3.0 is a greenfield rewrite with no production users, we are designing the optimal API from scratch without backward compatibility concerns.

### 11.1 Core API Philosophy

v3.0 follows these principles:

- **Simple is better than complex**: Minimal boilerplate, intuitive defaults
- **Explicit is better than implicit**: Clear configuration over magic
- **Standalone first**: Django/FastAPI are optional add-ons, not core dependencies

### 11.2 Standalone API (Primary)

```python
# Basic usage - simple and clean
from sqlery import Queue, Worker, job

# Define job function
@job(queue='default')
def send_email(to, subject, body):
    # Send email logic
    pass

# Enqueue job
job_instance = send_email.delay('user@example.com', 'Hello', 'Body')

# Run worker
worker = Worker(queues=['default'])
worker.work()
```

### 11.3 Django Integration API

```python
# Django users get native Django ORM integration
from sqlery import job

@job(queue='default')
def process_order(order_id):
    from myapp.models import Order
    order = Order.objects.get(id=order_id)
    # Process order
```

### 11.4 FastAPI Integration API

```python
# FastAPI users get async support
from sqlery import AsyncQueue, async_job

@async_job(queue='default')
async def process_data(data_id):
    # Async processing logic
    await async_operation()

# Enqueue from FastAPI endpoint
@app.post("/process")
async def enqueue_processing(data_id: int):
    job = await process_data.delay(data_id)
    return {"job_id": str(job.id)}
```

### 11.5 Design Decisions

Key API decisions for v3.0:

- **Backend Factory Pattern**: Simplifies backend creation and configuration
- **Dual Sync/Async APIs**: Separate classes for sync (`Queue`) and async (`AsyncQueue`) workflows
- **Smart Decorators**: `@job` decorator auto-detects sync vs async functions
- **Configuration First**: Support for configuration files (`sqlery.yml`) to reduce boilerplate
- **Type Safety**: Full type hints for IDE support and static analysis

---

## 12. Success Metrics

### 12.1 Technical Metrics

- **Core package size**: < 500 KB (no ORM dependencies)
- **Core dependencies**: ≤ 5 packages
- **Installation time**: < 10 seconds for core
- **Test coverage**: ≥ 90%
- **Performance**: ≥ 1000 jobs/second (single worker)

### 12.2 User Experience Metrics

- **Time to first job**: < 5 minutes (quickstart guide)
- **Documentation completeness**: All features documented
- **Migration success rate**: ≥ 95% (v2 → v3)
- **Issue resolution time**: < 48 hours median

### 12.3 Community Metrics

- **GitHub stars**: Track growth after v3.0 release
- **PyPI downloads**: Track adoption of v3.0
- **Stack Overflow questions**: Measure user engagement
- **Integration packages**: Track Django/FastAPI plugin usage

---

## 13. Future Enhancements

### 13.1 Additional Backends

- **Redis Backend**: For in-memory queue (like RQ)
- **MySQL Backend**: Raw SQL implementation
- **SQLite Backend**: Enhanced for edge computing

### 13.2 Additional Integrations

- **Flask Integration**: Flask-Sqlery plugin
- **Starlette Integration**: ASGI framework support
- **Litestar Integration**: Modern async framework

### 13.3 Advanced Features

- **Job Batching**: Group related jobs
- **Job Chains**: Sequential job execution
- **Job Graphs**: DAG-based workflows
- **Distributed Locking**: Across multiple workers
- **Metrics & Monitoring**: Prometheus exporter
- **OpenTelemetry**: Distributed tracing

### 13.4 Performance Optimizations

- **Connection Pooling**: Reuse database connections
- **Batch Operations**: Bulk job insertion
- **Async Workers**: AsyncIO support
- **Job Prefetching**: Reduce database queries

---

## 14. Risks & Mitigations

### 14.1 Architectural Complexity

**Risk**: Supporting both sync and async APIs may increase code complexity.

**Mitigation**:
- Use clear separation between sync and async implementations
- Share common logic through abstract base classes
- Comprehensive tests for both sync and async code paths
- Document when to use sync vs async clearly

### 14.2 Performance Regression

**Risk**: Raw SQL may be slower than ORM in some cases.

**Mitigation**:
- Benchmark before and after migration
- Profile hot paths and optimize
- Use prepared statements and connection pooling
- Load test with realistic workloads

### 14.3 Database Compatibility

**Risk**: Raw SQL may not work across different databases.

**Mitigation**:
- Support PostgreSQL and SQLite initially (most common)
- Abstract SQL dialect differences in backend layer
- Provide database-specific implementations
- Test against multiple database versions

### 14.4 Schema Evolution Complexity

**Risk**: Raw SQL migrations may be harder to maintain than ORM migrations.

**Mitigation**:
- Simple migration file format (numbered SQL files)
- Migration runner with status checking and validation
- Automatic migration generation for Django integration (SQL → Django)
- CLI commands to check migration status and create new migrations
- Rollback support for failed migrations
- Template files for common migration patterns

---

## 15. Conclusion

### 15.1 Summary

This plan transforms sqlery from a Django-centric job queue into a **standalone library** with optional integrations, following the design philosophy of RQ while maintaining sqlery's advanced features.

**Key Changes**:
1. **Core Package**: Standalone, ORM-free, minimal dependencies
2. **Raw SQL Migrations**: No Alembic/Django migrations in core
3. **Backend Abstraction**: PostgreSQL and SQLite backends with raw SQL
4. **Plugin Architecture**: Django and FastAPI as optional integrations
5. **Clean API**: Simple imports, RQ-like interface
6. **Comprehensive Testing**: Separate test suites for core and integrations

### 15.2 Benefits

**For Users**:
- Faster installation (no ORM dependencies)
- Simpler deployment (fewer dependencies)
- Better performance (optimized raw SQL)
- More flexibility (use with any framework)

**For Maintainers**:
- Easier to maintain (clear separation of concerns)
- Easier to test (core tests don't need Django)
- Easier to extend (plugin architecture)
- Better code quality (single responsibility principle)

### 15.3 Timeline

- **Phase 1-2** (Weeks 1-4): Foundation and reorganization
- **Phase 3-4** (Weeks 5-6): API cleanup and dependency removal
- **Phase 5-6** (Weeks 7-8): Documentation and testing
- **Phase 7** (Week 9): Release v3.0

**Total: 9 weeks from start to release**

### 15.4 Next Steps

1. Review and approve this plan
2. Create GitHub project board with tasks
3. Begin Phase 1 implementation
4. Set up CI for core-only tests
5. Create migration guide draft

---

## Appendix A: Comparison with RQ

| Feature | RQ | Sqlery v2.x | Sqlery v3.0 (Target) |
|---------|-----|-------------|----------------------|
| **Core Dependencies** | Redis | Django/SQLAlchemy | PostgreSQL driver only |
| **Framework Agnostic** | ✅ Yes | ⚠️ Django-centric | ✅ Yes |
| **Built-in Scheduling** | ❌ No | ✅ Yes | ✅ Yes |
| **Cron Jobs** | ❌ No | ✅ Yes | ✅ Yes |
| **Web UI** | External (rq-dashboard) | ✅ Built-in | ✅ Built-in (optional) |
| **Migrations** | N/A (Redis) | Django/Alembic | ✅ Raw SQL |
| **ORM Support** | N/A | Django ORM | ✅ Optional (plugin) |
| **Rate Limiting** | ❌ No | ✅ Yes | ✅ Yes |
| **Job Dependencies** | ✅ Yes | ✅ Yes | ✅ Yes |
| **Optimistic Locking** | ❌ No | ✅ Yes | ✅ Yes |
| **Multi-queue Workers** | ✅ Yes | ✅ Yes | ✅ Yes |
| **Persistence** | ⚠️ Redis (volatile) | ✅ SQL (durable) | ✅ SQL (durable) |

**Conclusion**: Sqlery v3.0 combines RQ's standalone design with advanced features for production use.

---

## Appendix B: Example Project Structure

Example project using standalone sqlery:

```
myproject/
├── requirements.txt          # sqlery (core only)
├── sqlery.yml                # Sqlery config
├── tasks.py                  # Job definitions
├── main.py                   # Application entry point
└── worker.py                 # Worker script

# requirements.txt
sqlery>=3.0.0
fastapi>=0.104.0

# tasks.py
from sqlery import job

@job(queue='default')
def send_email(to, subject, body):
    # Email logic
    pass

@job(queue='reports', cron='0 2 * * *')
def generate_daily_report():
    # Report logic
    pass

# main.py
from fastapi import FastAPI
from tasks import send_email

app = FastAPI()

@app.post("/send-email")
def enqueue_email(to: str, subject: str, body: str):
    job = send_email.delay(to, subject, body)
    return {"job_id": str(job.id)}

# worker.py
from sqlery import Worker, Queue

if __name__ == '__main__':
    queue = Queue(
        name='default',
        connection='postgresql://localhost/myapp'
    )
    worker = Worker([queue])
    worker.work()
```

**Run**:
```bash
# Start worker
sqlery worker --queue default

# Or use Python script
python worker.py

# Start scheduler
sqlery scheduler

# Start web UI (optional)
sqlery web --port 8001
```

---

**End of Plan**
