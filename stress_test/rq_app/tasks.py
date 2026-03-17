"""RQ task definition — wraps the shared benchmark job."""

from shared_job import increment_and_wait  # noqa: F401 — re-exported for rq

# RQ discovers this function by its import path:
# rq_app.tasks.increment_and_wait
