### `t/unit/bin/test_control.py`

Operator CLI contracts: querying and commanding running workers, and reporting problems. Portable guarantee: the ops CLI never dumps a stack trace for expected failures, uses a distinct exit code for "unavailable", and never leaks secrets.

**Remote commands to workers**
- Register a custom query command and a custom control command; run each with a zero timeout against a system with no workers; expect: exit code "unavailable" and the single message "No nodes replied within time constraint".
- Same with a global preload option (working directory) given before the command; expect: identical behaviour, preload options do not break the command.

**Bad input**
- Ask for a command that does not exist (query or control); expect: usage-error exit code (2) and a message naming the command plus the list of available commands of that kind.
- Ask for a command that exists but is the wrong kind (query command run as control, or the reverse); expect: same "not recognized" usage error.
- List available commands for query and for control; expect: exit 0, each custom command shown with its argument names and description, only for its own kind.

**Backend (database/broker) unreachable**
- Worker-status command while the backend is unreachable; expect: "unavailable" exit code, friendly "could not connect" message with the reason, no traceback.
- Worker-graph command while the backend is unreachable; expect: same friendly message and reason.
- Query command (sending the request fails with connection error); expect: friendly connection message, reason, no traceback.
- Control command (broadcast fails with connection error); expect: same.
- Event dump, event camera, and live event monitor, each failing with connection error; expect: same friendly message and reason, no traceback.

**Unexpected errors**
- Status command hits an arbitrary runtime error; expect: "unavailable" exit code, one-line summary "Unable to run the status command. Reason: <msg>", no traceback.
- Query command hits an arbitrary runtime error; expect: summary naming the full command (e.g. "inspect <name>") and reason, no traceback.
- Control command hits an arbitrary runtime error; expect: same, naming the control command.
- The error handler receives an already-formed CLI exception; expect: it is re-raised unchanged (same object), not wrapped or swallowed.

**Secret hygiene in the diagnostic report**
- Report with a multi-host result backend URL (no password); expect: exit 0, URL shown in full.
- Report with multi-host URLs each carrying a password; expect: every password masked, each host kept intact.
- Report with a password containing commas and "@" (with and without username); expect: whole password masked, username and host preserved, no fragment of the password in output.
- Config mutated during the report is restored afterwards (no leakage between runs).
