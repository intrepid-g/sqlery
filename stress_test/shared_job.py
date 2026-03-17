"""The benchmark job — identical logic used by both sqlery and rq."""

import time


def increment_and_wait(job_number: int) -> dict:
    """Simulate light work: increment a counter and wait 200ms."""
    # time.sleep(0.2)
    return {"job_number": job_number, "status": "done"}
