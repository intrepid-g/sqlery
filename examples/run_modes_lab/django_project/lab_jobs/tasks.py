import logging
import time

from sqlery import job

logger = logging.getLogger(__name__)


@job
def ping_job(mode: str) -> dict:
    """Record that `mode`'s execution path successfully ran a job."""
    logger.info("ping_job executed for mode=%s", mode)
    return {"mode": mode, "ts": time.time()}
