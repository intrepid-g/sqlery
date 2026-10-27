### `t/unit/app/test_preload_cli.py`

This file covers CLI "preload options": extra options a project registers on the app (for example an ini file path) that every command must accept and broadcast to listeners. Portable guarantee for sqlery: project-level extension options are accepted by every worker/admin command, delivered once to listeners, and never break commands that did not ask for them.

**Acceptance of extra options**
- Run a built-in command (purge, shell) with an extension option that the app did not register; expect: rejected with "No such option" naming the option, exit code 2.
- Run the same command against an app that registered the extension option; expect: accepted, exit code 0.
- Run a third-party plugin command that declares no extra parameters, passing a registered extension option; expect: command runs once, and listeners receive the option value.
- Run an in-tree command that uses the preload helper but takes no extra parameters; expect: runs, listeners receive the value.
- Run the report command with an extension option; expect: exit code 0.

**Commands that ignore extensions**
- Run a plugin command with no extension options given; expect: it still runs normally.

**Command's own options are preserved**
- Pass a command-specific flag (force) together with an extension option; expect: the command sees force=true, listeners see the extension value, nothing of the command's own options is stripped.

**No duplicate delivery**
- Invoke the CLI twice in a row for the same plugin command; expect: listeners notified once per invocation (2 total), and the command is wrapped only once (no stacked wrappers).
- Apply the preload wrapper twice to the same function and call it; expect: function runs once and listeners are notified exactly once.

**What the command receives**
- Command accepts arbitrary extra keyword arguments; expect: it receives all extension values (ini and ini-var), and listeners receive them too.
- Command declares only one of the extension names explicitly; expect: it gets that one, the unknown extension is dropped from its arguments, but listeners still receive all values.

**Edge cases and bad input**
- Call a wrapped command directly without supplying extension values; expect: no error (no missing-key failure), command runs, listeners are not notified.
- Extension options with a default value, a boolean flag, and an empty-string value; expect: listeners receive the empty string as given, the default for the unset option, and true for the flag; command still runs.

**Test isolation (not a product guarantee)**
- Registered commands' parameter lists and callbacks are restored after each invocation so one run cannot leak extensions into the next.
