"""Standalone mode task definitions for the run_modes_lab.

This module defines job functions that test sqlery's execution across all modes.
The @job decorator works in both Django and standalone modes with identical API.
"""

import logging
import time

from sqlery.core.job import job

logger = logging.getLogger(__name__)


@job
def ping_job(mode: str) -> dict:
    """Record that `mode`'s execution path successfully ran a job.

    This function is called by each of the 6 execution modes (daemon, subprocess,
    thread, http-trigger, lambda-sim, async-worker) plus the standalone mode,
    for a total of 7 queues tested. The return value is stored in the job's
    `output` field and can be verified by the verifier script.

    Args:
        mode: The execution mode name (e.g., 'daemon', 'subprocess', etc.)

    Returns:
        A dictionary with mode and timestamp for verification.
    """
    logger.info("ping_job executed for mode=%s", mode)
    return {"mode": mode, "ts": time.time()}
