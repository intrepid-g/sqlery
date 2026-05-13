"""Tests for the AsyncDatabaseBackend ABC (ASYN-01).

Verifies:
- Importable from sqlery.compat.
- Abstract (cannot be instantiated directly).
- A trivial concrete subclass can be instantiated.
- Every required hot-path method is present, abstract, and a coroutine function.
"""

import inspect

import pytest


REQUIRED_METHODS = {
    "aclaim_job",
    "amark_running",
    "amark_success",
    "amark_failed",
    "amark_shutting_down",
    "aget_status",
    "aget_job",
    "aupdate_heartbeat",
    "aregister_worker",
    "aunregister_worker",
    "aclaim_lease",
    "arenew_lease",
    "arelease_lease",
    "aget_due_scheduled_tasks",
    "aregistry_add",
    "aregistry_remove",
}


def test_import_async_database_backend():
    from sqlery.compat import AsyncDatabaseBackend  # noqa: F401


def test_async_database_backend_is_abstract():
    from sqlery.compat import AsyncDatabaseBackend

    with pytest.raises(TypeError):
        AsyncDatabaseBackend()


def test_trivial_subclass_instantiable():
    from sqlery.compat import AsyncDatabaseBackend

    method_names = REQUIRED_METHODS

    namespace = {}
    for name in method_names:
        async def _m(self, *a, _name=name, **kw):
            return None

        _m.__name__ = name
        namespace[name] = _m

    Concrete = type("Concrete", (AsyncDatabaseBackend,), namespace)
    instance = Concrete()
    assert isinstance(instance, AsyncDatabaseBackend)


def test_all_required_methods_present_and_async():
    from sqlery.compat import AsyncDatabaseBackend

    abstract = set(AsyncDatabaseBackend.__abstractmethods__)
    missing = REQUIRED_METHODS - abstract
    assert not missing, f"Missing abstract methods: {missing}"

    for name in REQUIRED_METHODS:
        attr = getattr(AsyncDatabaseBackend, name)
        assert inspect.iscoroutinefunction(attr), f"{name} must be async def"


def test_in_module_all():
    from sqlery import compat

    assert "AsyncDatabaseBackend" in compat.__all__
