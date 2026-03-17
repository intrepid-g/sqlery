"""Setup database tables for the example."""

import asyncio
from sqlery.backends import BackendFactory
from sqlery.schema import create_tables_async


async def setup():
    """Create required database tables."""
    print("Creating database tables...")

    backend = BackendFactory.create_async_backend('sqlite:///example.db')
    await backend.connect()

    await create_tables_async(backend)

    await backend.disconnect()
    print("✓ Database tables created successfully")


if __name__ == '__main__':
    asyncio.run(setup())
