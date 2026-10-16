"""chain / group / chord over dependencies, standalone and Django."""

import pytest

from sqlery import chain, chord, group
from sqlery.core.job import job
from sqlery.core.result import TaskFailedError
from sqlery.core.worker import JobExecutor
from sqlery.django_sqlery.decorators import job as djob


@job
def add(a, b):
    return a + b


@job
def total(numbers):
    return sum(numbers)


@job
def const(x):
    return x


@job
def boom(prev=None):
    raise RuntimeError("step failed")


@job(bind=True, max_retries=2, retry_backoff=0)
def flaky(self, x):
    if self.request.retries == 0:
        self.retry(exc=RuntimeError("first try"), countdown=0)
    return x * 100


@djob
def dadd(a, b=0):
    return a + b


@pytest.fixture
def backend(tmp_path):
    from sqlery.fastapi_sqlery import database
    from sqlery.fastapi_sqlery.backend import SQLAlchemyBackend

    database.init_database(f"sqlite:///{tmp_path / 'c.db'}")
    b = SQLAlchemyBackend()
    import sqlery.compat as compat

    compat._backend = b  # AsyncResult/apply paths use get_backend()
    yield b
    compat._backend = None


def drain(backend, limit=50):
    ex = JobExecutor(backend=backend)
    for _ in range(limit):
        j = backend.claim_job(["default"], "w")
        if j is None:
            return
        ex.execute_job(j)


def test_chain_passes_results_and_si_ignores(backend):
    res = (add.s(1, 2) | add.s(10) | const.si(7) | add.s(1)).apply_async()
    drain(backend)
    assert res.get(timeout=1) == 8           # 7 + 1: const.si ignored upstream 13
    first = res.parent.parent.parent
    assert first.get(timeout=1) == 3 and res.parent.parent.get(timeout=1) == 13


def test_group_returns_results_in_order(backend):
    res = group(add.s(i, i) for i in range(4)).apply_async()
    assert len(res) == 4
    drain(backend)
    assert res.ready() and res.successful()
    assert res.get(timeout=1) == [0, 2, 4, 6]


def test_chord_body_gets_list_of_header_results(backend):
    res = chord(group(add.s(i, 1) for i in range(3)), total.s()).apply_async()
    assert res.state == "PENDING"
    drain(backend)
    assert res.get(timeout=1) == 6           # (0+1)+(1+1)+(2+1)
    assert res.parent.get(timeout=1) == [1, 2, 3]


def test_group_inside_chain_becomes_chord(backend):
    res = chain(const.si(5), group(add.s(1), add.s(2)), total.s()).apply_async()
    drain(backend)
    assert res.get(timeout=1) == (5 + 1) + (5 + 2)


def test_failure_stops_the_chain(backend):
    res = (boom.s() | add.s(1, 2) | const.s()).apply_async()
    drain(backend)
    assert res.state == "FAILURE"
    assert res.parent.state == "FAILURE" and res.parent.parent.state == "FAILURE"
    with pytest.raises(TaskFailedError):
        res.get(timeout=1)
    assert "Dependencies failed" in res.error


def test_step_that_retries_does_not_break_the_chain(backend):
    res = (flaky.s(3) | add.s(1)).apply_async()
    drain(backend)
    assert res.get(timeout=1) == 301         # retry result (300) flowed downstream


def test_dependency_waits_while_upstream_is_queued(backend):
    res = (const.s(1) | add.s(1)).apply_async()
    assert backend.claim_job(["default"], "w").id == res.parent.id   # only the first is claimable
    assert backend.claim_job(["default"], "w") is None


# ---- Django ----------------------------------------------------------------------------
@pytest.mark.django_db
def test_django_chain_binds_args_after_upstream_result():
    from sqlery.compat import get_backend
    from sqlery.core.claiming import claim_next_job_with_queue_priority
    from sqlery.executor import TaskExecutor

    backend = get_backend()
    res = chain(dadd.s(2, b=3), dadd.s(b=10)).apply_async()
    ex = TaskExecutor()
    for _ in range(4):
        j = claim_next_job_with_queue_priority(None, backend, queues=["default"])
        if j is None:
            break
        ex.execute_job(j)
    assert res.parent.get(timeout=1) == 5
    assert res.get(timeout=1) == 15          # upstream 5 -> a, b=10
