"""#37: every name `sqlery.fastapi_sqlery` promises in __all__ exists and imports."""

import warnings

import sqlery.fastapi_sqlery as pkg


def test_every_advertised_export_exists():
    assert "default_app" in pkg.__all__ and "SQLAlchemyBackend" in pkg.__all__
    assert all(hasattr(pkg, name) for name in pkg.__all__)


def test_importing_the_package_does_not_warn_that_fastapi_is_missing():
    import importlib

    with warnings.catch_warnings():
        warnings.simplefilter("error", ImportWarning)
        importlib.reload(pkg)
