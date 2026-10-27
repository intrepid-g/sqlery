"""Fork daemon workers from a preloaded launcher, like Celery's prefork pool (issue #163).

``WORKER_START_METHOD = "fork"`` (the default): the daemon forks one launcher process. The
launcher imports the task modules (``WORKER_PRELOAD_MODULES``, or each installed Django app's
``tasks`` module), drops every DB connection, freezes the garbage collector and then forks a
worker whenever the daemon asks for one. The workers share the interpreter, Django or
SQLAlchemy and the task modules with the launcher copy-on-write, instead of each importing
them again.

The launcher holds no DB connection, lease or scheduler state, so a worker inherits none of
the daemon's. It reaps the workers it forks and reports each exit to the daemon, which
tracks them through :class:`LaunchedWorker` handles shaped like ``subprocess.Popen``.

``WORKER_START_METHOD = "spawn"`` starts each worker as a fresh interpreter
(``python -m sqlery.core.worker_runner``), as before #163: slower and heavier, but a
respawned worker runs the code on disk now, not the code the daemon started with.
"""

from __future__ import annotations

import gc
import importlib
import json
import logging
import os
import select
import signal
import subprocess
import sys
import time

from .pool import encode_frame, read_frame, write_all

logger = logging.getLogger(__name__)

START_FORK = "fork"
START_SPAWN = "spawn"
START_METHODS = (START_FORK, START_SPAWN)
START_METHOD_ENV = "SQLERY_WORKER_START_METHOD"
PRELOAD_ENV = "SQLERY_WORKER_PRELOAD_MODULES"

# How long the daemon waits for the launcher to report a forked worker's PID.
SPAWN_REPLY_SECONDS = 10.0
# Launcher loop tick: the longest an exited worker stays an unreaped zombie.
LAUNCHER_TICK_SECONDS = 0.2


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


def resolve_start_method(value, can_fork: bool = True) -> str:
    """``"fork"`` or ``"spawn"``; unknown values fall back to fork, no ``os.fork`` to spawn."""
    name = str(value or START_FORK).strip().lower()
    if name not in START_METHODS:
        logger.warning(f"Unknown WORKER_START_METHOD {value!r}; using {START_FORK!r}")
        name = START_FORK
    return name if can_fork else START_SPAWN


def parse_module_list(value) -> list[str] | None:
    """A module list from a setting (list, tuple or comma-separated string); None if unset."""
    if value is None:
        return None
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [str(part).strip() for part in value if str(part).strip()]


def exit_code(status: int) -> int:
    """``os.waitpid`` status as a ``Popen.returncode`` (negative signal number if killed)."""
    if os.WIFSIGNALED(status):
        return -os.WTERMSIG(status)
    return os.WEXITSTATUS(status) if os.WIFEXITED(status) else 1


def orphan_returncode(pid: int) -> int | None:
    """Exit code of a worker whose launcher died, or None while it still runs.

    The orphan is re-parented to the nearest subreaper or init. If that is this process
    (a daemon running as a container's PID 1), it is reaped here; otherwise an exited
    orphan another parent never reaps stays a zombie, which counts as exited.
    """
    try:
        reaped, status = os.waitpid(pid, os.WNOHANG)
        if reaped:
            return exit_code(status)
    except ChildProcessError:
        pass  # not our child
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return -1
    except PermissionError:
        return None
    try:
        with open(f"/proc/{pid}/stat") as f:
            state = f.read().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return None
    return -1 if state in ("Z", "X") else None


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def _config(name: str, env: str, default=None):
    raw = os.environ.get(env)
    if raw is not None and raw.strip() != "":
        return raw.strip()
    from ..compat import get_config

    return get_config(name, default)


def load_start_method() -> str:
    """``SQLERY_WORKER_START_METHOD``, else ``WORKER_START_METHOD``, else fork (Celery)."""
    return resolve_start_method(
        _config("WORKER_START_METHOD", START_METHOD_ENV, START_FORK), hasattr(os, "fork")
    )


def preload_module_names() -> list[str]:
    """Modules the launcher imports before forking workers.

    ``WORKER_PRELOAD_MODULES`` (or ``SQLERY_WORKER_PRELOAD_MODULES``) when set; otherwise, in
    Django mode, the ``tasks`` module of every installed app that has one (what Celery's
    ``app.autodiscover_tasks()`` imports). Set it to ``[]`` to preload nothing.
    """
    names = parse_module_list(_config("WORKER_PRELOAD_MODULES", PRELOAD_ENV, None))
    if names is not None:
        return names
    try:
        from django.apps import apps
        from django.conf import settings
        from django.utils.module_loading import module_has_submodule
    except ImportError:
        return []
    if not settings.configured or not apps.ready:
        return []
    return [
        f"{app.name}.tasks"
        for app in apps.get_app_configs()
        if module_has_submodule(app.module, "tasks")
    ]


def preload_modules(names: list[str]) -> list[str]:
    """Import ``names``; a module that fails to import is logged and skipped."""
    loaded = []
    for name in names:
        try:
            importlib.import_module(name)
            loaded.append(name)
        except Exception as e:
            logger.error(f"Launcher could not preload {name!r}: {e}")
    return loaded


# ---------------------------------------------------------------------------
# Daemon side
# ---------------------------------------------------------------------------


class LaunchedWorker:
    """A worker forked by the launcher, with the parts of ``subprocess.Popen`` the pool uses.

    The launcher (its parent) reaps it and reports the exit code; ``poll()`` reads it.
    """

    def __init__(self, pid: int, launcher: WorkerLauncher):
        self.pid = pid
        self.returncode: int | None = None
        self._launcher = launcher
        self._launcher_pid = launcher.pid  # the launcher that forked (and reaps) it

    def poll(self) -> int | None:
        if self.returncode is None:
            self._launcher.drain()
        if self.returncode is None and (
            not self._launcher.alive() or self._launcher.pid != self._launcher_pid
        ):
            # Orphaned by a dead launcher (maybe since replaced): no exit report will come
            # for it, so ask the kernel.
            self.returncode = orphan_returncode(self.pid)
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        deadline = None if timeout is None else time.monotonic() + timeout
        while self.poll() is None:
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(f"worker {self.pid}", timeout)
            time.sleep(0.05)
        return self.returncode

    def send_signal(self, sig: int) -> None:
        if self.poll() is None:
            os.kill(self.pid, sig)

    def terminate(self) -> None:
        self.send_signal(signal.SIGTERM)

    def kill(self) -> None:
        self.send_signal(signal.SIGKILL)


class WorkerLauncher:
    """The daemon's handle on the launcher process. Started on the first :meth:`spawn`."""

    def __init__(self, stdout_path: str | None = None):
        self.pid: int | None = None
        self._request_fd: int | None = None
        self._reply_fd: int | None = None
        self._workers: dict[int, LaunchedWorker] = {}
        self._stdout_path = stdout_path  # worker stdout (debug mode log), else /dev/null

    # --- lifecycle ---

    def start(self) -> None:
        from .fork_safety import ForkSafeExecutor

        request_r, request_w = os.pipe()
        reply_r, reply_w = os.pipe()
        # Fork hooks close the daemon's DB connections first: the launcher inherits none.
        pid = ForkSafeExecutor.auto_configure().fork()
        if pid == 0:
            code = 1
            try:
                os.close(request_w)
                os.close(reply_r)
                code = _launcher_main(request_r, reply_w, self._stdout_path)
            except BaseException:
                pass
            os._exit(code)
        os.close(request_r)
        os.close(reply_w)
        self.pid, self._request_fd, self._reply_fd = pid, request_w, reply_r
        logger.info(f"Started worker launcher PID {pid}")

    def alive(self) -> bool:
        if self.pid is None:
            return False
        try:
            pid, _ = os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            pid = self.pid
        if pid != 0:
            self._forget()
            return False
        return True

    def close(self, timeout: float = 2.0) -> None:
        """Stop the launcher (closing its pipe). Workers it forked keep running."""
        if self.pid is None:
            return
        pid = self.pid
        self._close_fds()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if os.waitpid(pid, os.WNOHANG)[0] != 0:
                    break
            except ChildProcessError:
                break
            time.sleep(0.02)
        else:
            try:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            except (OSError, ChildProcessError):
                pass
        self.pid = None

    def _close_fds(self) -> None:
        for fd in (self._request_fd, self._reply_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self._request_fd = self._reply_fd = None

    def _forget(self) -> None:
        self._close_fds()
        self.pid = None

    # --- requests ---

    def spawn(self, queues: list[str]) -> LaunchedWorker:
        """Have the launcher fork a worker for ``queues``; returns its handle."""
        if not self.alive():
            self.start()
        try:
            write_all(self._request_fd, encode_frame(("spawn", list(queues))))
        except BrokenPipeError:
            self._forget()
            self.start()
            write_all(self._request_fd, encode_frame(("spawn", list(queues))))
        deadline = time.monotonic() + SPAWN_REPLY_SECONDS
        while time.monotonic() < deadline:
            readable, _, _ = select.select([self._reply_fd], [], [], 0.1)
            if not readable:
                continue
            message = read_frame(self._reply_fd)
            if message is None:
                self._forget()
                raise RuntimeError("worker launcher exited")
            if message[0] == "spawned":
                worker = LaunchedWorker(message[1], self)
                self._workers[worker.pid] = worker
                return worker
            if message[0] == "error":
                raise RuntimeError(f"worker launcher could not fork: {message[1]}")
            self._handle(message)
        raise TimeoutError("worker launcher did not answer")

    def drain(self) -> None:
        """Read the exit reports the launcher has sent so far."""
        while self._reply_fd is not None:
            readable, _, _ = select.select([self._reply_fd], [], [], 0)
            if not readable:
                return
            message = read_frame(self._reply_fd)
            if message is None:
                self._forget()
                return
            self._handle(message)

    def _handle(self, message) -> None:
        if message[0] == "exited":
            worker = self._workers.pop(message[1], None)
            if worker is not None:
                worker.returncode = message[2]


# ---------------------------------------------------------------------------
# Launcher side
# ---------------------------------------------------------------------------


def _launcher_main(request_fd: int, reply_fd: int, stdout_path: str | None) -> int:
    """Preload, then fork a worker per request and report exits, until the daemon is gone."""
    from .db_resilience import discard_connections

    os.setsid()  # out of the daemon's process group: a terminal's Ctrl-C doesn't reach it
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGUSR1, signal.SIG_IGN)
    loaded = preload_modules(preload_module_names())
    logger.info(f"Worker launcher PID {os.getpid()} preloaded {loaded or 'no modules'}")
    discard_connections()  # a module may have queried at import: share no connection
    gc.freeze()  # keep the collector from writing to (and unsharing) preloaded objects

    while True:
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            if pid == 0:
                break
            write_all(reply_fd, encode_frame(("exited", pid, exit_code(status))))
        readable, _, _ = select.select([request_fd], [], [], LAUNCHER_TICK_SECONDS)
        if not readable:
            continue
        request = read_frame(request_fd)
        if request is None:
            return 0  # the daemon closed the pipe (or died)
        if request[0] != "spawn":
            continue
        try:
            pid = _fork_worker(request[1], request_fd, reply_fd, stdout_path)
            reply = ("spawned", pid)
        except Exception as e:
            reply = ("error", str(e))
        write_all(reply_fd, encode_frame(reply))


def _fork_worker(queues: list[str], request_fd: int, reply_fd: int, stdout_path) -> int:
    from .fork_safety import ForkSafeExecutor
    from .worker_pool import WORKER_QUEUES_ENV

    pid = ForkSafeExecutor.auto_configure().fork()
    if pid != 0:
        return pid
    # === WORKER ===
    code = 1
    try:
        os.setsid()  # its own session, as spawn's start_new_session=True
        os.close(request_fd)
        os.close(reply_fd)
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGUSR1):
            signal.signal(signum, signal.SIG_DFL)
        with open(stdout_path or os.devnull, "a") as out:
            os.dup2(out.fileno(), sys.stdout.fileno())
        os.environ[WORKER_QUEUES_ENV] = json.dumps(list(queues))
        root = logging.getLogger()
        for handler in root.handlers[:]:  # the daemon's; the worker logs to its own file
            root.removeHandler(handler)
        from .worker_runner import run_worker

        run_worker()
        code = 0
    except BaseException:
        logging.getLogger(__name__).exception("Forked worker crashed")
    finally:
        os._exit(code)
