### `t/unit/utils/test_platforms.py`

Mostly OS-level process hygiene for a worker daemon (pidfile, privileges, signals, fds, detaching). Portable guarantees only.

**Worker single-instance lock (pidfile)**
- Start a worker with a pidfile that is held by a live process; expect: startup refused with an "already exists" message, exit non-zero.
- Pidfile points to a dead process; expect: treated as stale, removed, new worker starts.
- Pidfile points to a process we are not allowed to signal (permission denied); expect: treated as stale and removed.
- Pidfile is empty/missing, contains garbage, or holds the current process's own pid; expect: treated as stale, removed.
- Pidfile partially written (no trailing newline) or non-numeric; expect: read fails loudly, never a wrong pid.
- Pidfile missing on read; expect: "no pid", not an error. Other I/O errors on read; expect: raised.
- Write the pidfile then re-read it; expect: contents match our pid, else lock failed (another worker raced us). Write failure; expect: lock-failed error.
- Pidfile written durably (fsync) and with restrictive flags/mode.
- Worker exits normally or crashes inside its context; expect: pidfile removed. Removing an already-missing or permission-denied file; expect: silently OK. Any other removal error; expect: raised.

**Privileges and security**
- Run worker as root without explicit force flag; expect: refused (security error). With force flag; expect: allowed with a security warning.
- Run as non-root user belonging to an admin group (sudo/wheel) without force; expect: refused. With force; expect: warning.
- Group lookup for the user fails; expect: warn "assuming root-like", still refuse without force / warn with force.
- Non-root, non-admin user; expect: no warnings. Unusual platform lacking uid/gid APIs; expect: refused as suspicious (except on Windows).
- Missing user/group database modules; expect: check skipped, no warnings.
- Drop privileges to a given user/group; expect: group set first, supplementary groups initialised, then user set.
- After dropping, regaining root succeeds; expect: security error (drop was not permanent). Regaining root denied; expect: OK.
- Drop with only a group; expect: group changed, user untouched. Unknown user/group name; expect: lookup error raised. Numeric ids accepted as-is.
- Setting user hits an unexpected OS error (not "permission denied"); expect: raised, not swallowed.
- Windows: dropping privileges is a no-op.
- Supplementary group list too long for the OS (hundreds of groups); expect: truncated to the system limit, or retried with fewer groups; not-permitted but current groups already cover the request; expect: tolerated; otherwise raised.

**Detaching / daemonising**
- Detach double-forks, starts a new session, sets umask, changes directory, redirects stdio to null; parent exits 0, child continues.
- Re-entering an already-open daemon context; expect: no second detach.
- "Fake" mode; expect: no forking, but post-chdir hook still runs.
- Umask accepts octal string ("0755"), decimal string, or int.
- Detaching without OS resource support; expect: runtime error.
- Detach with logfile: log opened for append (and closed) after chdir; with pidfile: lock taken after chdir; child-process signal for terminated children reset to default.
- Post-fork hooks of the multiprocessing layer run only when requested.
- Detaching completes promptly even with a huge fd limit (about 1e9, container case); expect: no scan of the whole range.

**File descriptors**
- Close all open fds except an explicit keep list (fds or file objects); expect: only the others closed.
- Closing an fd that is already closed; expect: ignored. Any other close error; expect: raised.
- Finding fds by path returns the right fd, including very high numbered ones (9000+); open failures on a path; expect: skipped.
- Prefers the per-process fd directory; falls back to a full scan when absent; fd directory not trusted on BSD without proper fd filesystem.
- Max fd: if the limit is infinite or unknown, use the default; else the real limit.

**Signals**
- Signals addressable by name with or without prefix, or number; unknown/lowercase name; expect: error. Check support of a signal name.
- Ignore, reset to default, install handler; handler install failing (e.g. not main thread); expect: swallowed.
- Arm/reset alarm timer (timeouts).
- Parent-death signal can be requested (child worker dies when parent dies); no-op where unsupported.

**Misc**
- Ignore a chosen errno (e.g. already-closed) but raise on any other.
- Process title set safely when title library missing; multiprocess title includes child name and optionally hostname.
- isatty tolerates objects without isatty.
- Command-line option lookup supports `--opt=value` and `-o value`.
