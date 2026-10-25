"""Bound on the ``run_jobs`` processes started by the subprocess trigger.

``MAX_SUBPROCESS_WORKERS`` slot files live in a directory. A spawner takes a
non-blocking ``flock`` on a free slot, hands the locked fd to the child
(``pass_fds``) and closes its own copy, so the lock lives exactly as long as the
child (and the chain of successors that inherit the fd) and the kernel releases it
even when the child is SIGKILLed or OOM-killed. Without ``fcntl`` (Windows) or with
``MAX_SUBPROCESS_WORKERS`` of 0/None the spawn is unbounded, as it always was.
"""

import hashlib
import logging
import os
import tempfile

from django.conf import settings as django_settings

from .settings import get_setting

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

logger = logging.getLogger(__name__)

SLOT_ENV = "SQLERY_SLOT_FD"  # "<fd>:<dev>:<inode>" of the slot lock a child inherited


class Slot:
    """A claimed slot (or none, when unbounded): extra Popen kwargs plus ``release()``."""

    def __init__(self, fd: int | None = None, owned: bool = False):
        self.fd = fd
        self._owned = owned

    def popen_kwargs(self, base_env=None) -> dict:
        """``env`` / ``pass_fds`` for ``subprocess.Popen`` so the child holds this slot."""
        env = dict(os.environ if base_env is None else base_env)
        env.pop(SLOT_ENV, None)
        if self.fd is None:
            return {"env": env}
        st = os.fstat(self.fd)
        env[SLOT_ENV] = f"{self.fd}:{st.st_dev}:{st.st_ino}"
        return {"env": env, "pass_fds": (self.fd,)}

    def release(self) -> None:
        """Close our copy (call after Popen); the child's copy keeps the lock."""
        if self._owned and self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:  # pragma: no cover
                pass
            self._owned = False


def max_subprocess_workers() -> int:
    """The configured bound; 0 means unbounded."""
    try:
        return max(0, int(get_setting("MAX_SUBPROCESS_WORKERS", 2) or 0))
    except (TypeError, ValueError):
        return 0


def slot_dir() -> str:
    """SUBPROCESS_SLOT_DIR, or a per-project directory under the system temp dir."""
    configured = get_setting("SUBPROCESS_SLOT_DIR", None)
    if configured:
        return str(configured)
    base_dir = os.path.realpath(str(getattr(django_settings, "BASE_DIR", "") or os.getcwd()))
    digest = hashlib.sha1(base_dir.encode()).hexdigest()[:12]
    return os.path.join(tempfile.gettempdir(), f"sqlery-slots-{digest}")


def _inherited_fd() -> int | None:
    """The slot fd this process inherited from its spawner, if it is still valid."""
    raw = os.environ.get(SLOT_ENV)
    if not raw or fcntl is None:
        return None
    try:
        fd, dev, ino = (int(part) for part in raw.split(":"))
        st = os.fstat(fd)
    except (ValueError, OSError):
        return None
    # The inode check rejects a stale variable that leaked into an unrelated process.
    if (st.st_dev, st.st_ino) != (dev, ino):
        return None
    # Keep the lock out of anything this process execs; pass_fds re-enables it for a successor.
    try:
        os.set_inheritable(fd, False)
    except OSError:  # pragma: no cover
        pass
    return fd


def protect_inherited_slot() -> None:
    """Make an inherited slot fd non-inheritable (call early in ``run_jobs``), so a job that
    execs a long-lived process with ``close_fds=False`` cannot keep the slot held."""
    _inherited_fd()


def claim_slot(inherit: bool = False) -> Slot | None:
    """Claim a slot for a child about to be spawned; ``None`` means the bound is reached.

    Args:
        inherit: A ``run_jobs`` chain passes the slot it already holds to its
            successor instead of taking a new one.
    """
    limit = max_subprocess_workers()
    if limit <= 0 or fcntl is None:
        return Slot()
    if inherit:
        fd = _inherited_fd()
        if fd is not None:
            return Slot(fd)
    try:
        directory = slot_dir()
        os.makedirs(directory, mode=0o700, exist_ok=True)
        for index in range(limit):
            fd = os.open(
                os.path.join(directory, f"slot-{index}.lock"), os.O_RDWR | os.O_CREAT, 0o600
            )
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                continue
            return Slot(fd, owned=True)
    except OSError as e:
        # Never let a broken slot directory stop job processing: fail open.
        logger.warning(f"Cannot use sqlery subprocess slots, spawning unbounded: {e}")
        return Slot()
    logger.debug(f"All {limit} run_jobs slots busy; not spawning")
    return None
