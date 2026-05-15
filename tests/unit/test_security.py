"""Unit tests for SEC-04: ALLOWED_TASK_MODULES opt-in allowlist."""

import logging

import pytest

from sqlery.core.security import (
    TaskModuleNotAllowed,
    check_task_module_allowed,
    is_production_env,
    warn_if_unconfigured,
)


# ===== check_task_module_allowed =====


class TestCheckTaskModuleAllowed:
    def test_none_allowed_is_passthrough(self):
        # BC: unset = allow all
        assert check_task_module_allowed("myapp.tasks", None) is None
        assert check_task_module_allowed("anything.at.all", None) is None

    def test_empty_list_is_passthrough(self):
        # Empty list = unset semantics
        assert check_task_module_allowed("myapp.tasks", []) is None

    def test_prefix_match_allowed(self):
        assert check_task_module_allowed("myapp.tasks", ["myapp"]) is None
        assert check_task_module_allowed("myapp.tasks.deep.path", ["myapp"]) is None

    def test_exact_match_allowed(self):
        assert check_task_module_allowed("myapp", ["myapp"]) is None
        assert check_task_module_allowed("myapp.tasks", ["myapp.tasks"]) is None

    def test_dot_boundary_prevents_prefix_collision(self):
        with pytest.raises(TaskModuleNotAllowed):
            check_task_module_allowed("myapp_evil.tasks", ["myapp"])

    def test_dot_boundary_prevents_substring_match(self):
        with pytest.raises(TaskModuleNotAllowed):
            check_task_module_allowed("evil_myapp.tasks", ["myapp"])

    def test_disallowed_module_raises(self):
        with pytest.raises(TaskModuleNotAllowed):
            check_task_module_allowed("other.tasks", ["myapp", "third"])

    def test_multi_entry_list_matches_any(self):
        assert check_task_module_allowed("third.tasks", ["myapp", "third"]) is None

    def test_error_message_includes_module(self):
        with pytest.raises(TaskModuleNotAllowed, match="other.tasks"):
            check_task_module_allowed("other.tasks", ["myapp"])


# ===== is_production_env =====


class TestIsProductionEnv:
    def test_env_production(self):
        assert is_production_env({"ENV": "production"}) is True

    def test_env_local(self):
        assert is_production_env({"ENV": "local"}) is False

    def test_environment_prod(self):
        assert is_production_env({"ENVIRONMENT": "prod"}) is True

    def test_django_settings_module_production(self):
        assert is_production_env(
            {"DJANGO_SETTINGS_MODULE": "myproj.settings_production"}
        ) is True

    def test_django_settings_module_dev(self):
        assert is_production_env({"DJANGO_SETTINGS_MODULE": "myproj.settings_dev"}) is False

    def test_case_insensitive(self):
        assert is_production_env({"ENV": "PRODUCTION"}) is True
        assert is_production_env({"ENV": "Prod"}) is True

    def test_empty_env(self):
        assert is_production_env({}) is False

    def test_defaults_to_os_environ(self, monkeypatch):
        monkeypatch.delenv("ENV", raising=False)
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        monkeypatch.delenv("DJANGO_SETTINGS_MODULE", raising=False)
        assert is_production_env() is False
        monkeypatch.setenv("ENV", "production")
        assert is_production_env() is True


# ===== warn_if_unconfigured =====


class TestWarnIfUnconfigured:
    def test_warns_once_in_production_when_unset(self, caplog, monkeypatch):
        monkeypatch.setenv("ENV", "production")
        with caplog.at_level(logging.WARNING, logger="sqlery.core.security"):
            warn_if_unconfigured(None)
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "ALLOWED_TASK_MODULES" in warnings[0].message

    def test_warns_when_empty_list_in_production(self, caplog, monkeypatch):
        monkeypatch.setenv("ENV", "production")
        with caplog.at_level(logging.WARNING, logger="sqlery.core.security"):
            warn_if_unconfigured([])
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1

    def test_no_warning_when_configured(self, caplog, monkeypatch):
        monkeypatch.setenv("ENV", "production")
        with caplog.at_level(logging.WARNING, logger="sqlery.core.security"):
            warn_if_unconfigured(["myapp"])
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 0

    def test_no_warning_when_not_production(self, caplog, monkeypatch):
        monkeypatch.delenv("ENV", raising=False)
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        monkeypatch.delenv("DJANGO_SETTINGS_MODULE", raising=False)
        with caplog.at_level(logging.WARNING, logger="sqlery.core.security"):
            warn_if_unconfigured(None)
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 0


# ===== Integration: worker dispatch enforcement =====


class TestWorkerDispatchEnforcement:
    """Verify the wired callsite in core/worker.py uses the allowlist."""

    def test_import_task_rejected_when_not_in_allowlist(self, monkeypatch):
        # Simulate enforcement at the dispatch site: when allowed is set and the
        # module is not in it, check_task_module_allowed raises before any import.
        with pytest.raises(TaskModuleNotAllowed):
            check_task_module_allowed("other.tasks", ["myapp"])

    def test_security_module_imported_by_worker(self):
        # Smoke test that the wiring import resolves.
        from sqlery.core import worker  # noqa: F401
        from sqlery.core.security import check_task_module_allowed  # noqa: F401

    def test_executor_import_task_gates_before_importlib(self, monkeypatch):
        """JobExecutor._import_task must raise TaskModuleNotAllowed BEFORE
        importlib.import_module is called when the module is outside the
        allowlist (verifies the gate placement, not just the primitive)."""
        from sqlery.core import utils as core_utils
        from sqlery.core.worker import JobExecutor

        called = {"import_module": False}

        def fail_if_called(*args, **kwargs):
            called["import_module"] = True
            raise AssertionError("importlib.import_module was reached")

        monkeypatch.setattr(core_utils, "import_module", fail_if_called)
        monkeypatch.setattr(
            "sqlery.core.worker.get_config",
            lambda key, default=None: ["myapp"] if key == "ALLOWED_TASK_MODULES" else default,
        )

        executor = JobExecutor.__new__(JobExecutor)  # skip __init__ (needs backend)
        with pytest.raises(TaskModuleNotAllowed):
            executor._import_task("other.tasks.do_thing")
        assert called["import_module"] is False

    def test_executor_import_task_passthrough_when_unset(self, monkeypatch):
        """BC: when ALLOWED_TASK_MODULES is unset, dispatch falls through to
        importlib (no TaskModuleNotAllowed raised)."""
        from sqlery.core.worker import JobExecutor

        monkeypatch.setattr(
            "sqlery.core.worker.get_config",
            lambda key, default=None: None if key == "ALLOWED_TASK_MODULES" else default,
        )

        executor = JobExecutor.__new__(JobExecutor)
        # Use a stdlib module path so import succeeds — we're asserting the
        # gate doesn't reject; the actual `os.path.join` is callable.
        fn = executor._import_task("os.path.join")
        assert callable(fn)


class TestWarnOncePerWorkerRun:
    """W3: production-env WARNING fires exactly once per WorkerProcess.run,
    pinned BEFORE the fork loop — not per forked job."""

    def test_warn_callsite_is_first_line_of_run(self):
        """Static check: the warn_if_unconfigured call appears before any
        loop / fork construct in WorkerProcess.run."""
        import inspect
        from sqlery.core.worker import WorkerProcess

        src = inspect.getsource(WorkerProcess.run)
        warn_idx = src.find("warn_if_unconfigured")
        assert warn_idx != -1, "warn_if_unconfigured must be called in run()"
        # Must appear before any 'while' loop and before any 'os.fork'.
        while_idx = src.find("while ")
        fork_idx = src.find("os.fork")
        if while_idx != -1:
            assert warn_idx < while_idx, "warn must precede the run loop"
        if fork_idx != -1:
            assert warn_idx < fork_idx, "warn must precede any fork"

    def test_warn_fires_exactly_once_across_n_dispatches(self, caplog, monkeypatch):
        """Simulate N forked job dispatches and assert the WARNING fires
        exactly once — by calling warn_if_unconfigured once (as run() does)
        and then invoking _import_task many times. The dispatch path must NOT
        re-trigger the warning."""
        from sqlery.core.worker import JobExecutor

        monkeypatch.setenv("ENV", "production")
        monkeypatch.setattr(
            "sqlery.core.worker.get_config",
            lambda key, default=None: None if key == "ALLOWED_TASK_MODULES" else default,
        )

        with caplog.at_level(logging.WARNING, logger="sqlery.core.security"):
            # Simulate WorkerProcess.run's first line:
            warn_if_unconfigured(None)
            # Simulate N forked dispatches:
            executor = JobExecutor.__new__(JobExecutor)
            for _ in range(10):
                executor._import_task("os.path.join")

        warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "ALLOWED_TASK_MODULES" in r.message
        ]
        assert len(warnings) == 1, (
            f"Expected exactly 1 WARNING across run + 10 dispatches, got {len(warnings)}"
        )


class TestConfigWiring:
    def test_django_defaults_has_key(self):
        from sqlery.django_sqlery.settings import DEFAULTS
        assert "ALLOWED_TASK_MODULES" in DEFAULTS
        assert DEFAULTS["ALLOWED_TASK_MODULES"] is None

    def test_standalone_config_default_none(self, monkeypatch):
        monkeypatch.delenv("SQLERY_ALLOWED_TASK_MODULES", raising=False)
        from sqlery.fastapi_sqlery.config import StandaloneConfig
        cfg = StandaloneConfig()
        assert cfg.get("ALLOWED_TASK_MODULES") is None

    def test_standalone_config_loads_env_var(self, monkeypatch):
        monkeypatch.setenv("SQLERY_ALLOWED_TASK_MODULES", "myapp, otherapp.tasks ,")
        from sqlery.fastapi_sqlery.config import StandaloneConfig
        cfg = StandaloneConfig()
        assert cfg.get("ALLOWED_TASK_MODULES") == ["myapp", "otherapp.tasks"]

    def test_standalone_config_empty_env_var_stays_none(self, monkeypatch):
        monkeypatch.setenv("SQLERY_ALLOWED_TASK_MODULES", "   ,  ,")
        from sqlery.fastapi_sqlery.config import StandaloneConfig
        cfg = StandaloneConfig()
        assert cfg.get("ALLOWED_TASK_MODULES") is None
