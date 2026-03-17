"""Sqlery task definition — wraps the shared benchmark job."""

from sqlery import job
from shared_job import increment_and_wait as _run


@job(queue="stress", timeout=30)
def increment_and_wait(job_number: int) -> dict:
    """Benchmark task registered with sqlery."""
    return _run(job_number)
