"""Worker process example."""

from sqlery import Worker
from sqlery.backends import BackendFactory
import tasks  # Import to register tasks


def main():
    """Run a worker to process jobs."""
    # Configure backend
    backend = BackendFactory.create_sync_backend('sqlite:///example.db')
    backend.connect()

    # Create worker for multiple queues
    # Processes 'default' and 'reports' queues
    worker = Worker(['default', 'reports'], backend=backend)

    print("=" * 60)
    print("Worker Starting")
    print("=" * 60)
    print()
    print("Processing queues: default, reports")
    print("Press Ctrl+C to stop")
    print()

    # Start processing jobs (runs forever)
    worker.work()


if __name__ == '__main__':
    main()
