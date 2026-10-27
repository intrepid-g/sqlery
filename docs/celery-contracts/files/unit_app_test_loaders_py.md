### `t/unit/app/test_loaders.py`

Loaders = how a worker finds its configuration and task modules at startup.

**Startup hooks**
- Start a worker with a loader whose hooks are no-ops (task init, worker init); expect: nothing breaks.
- Start a worker child process; expect: the child-process init hook is invoked exactly once.
- Start a worker with configured import modules; expect: all those modules are imported and loaded before it runs jobs.
- A startup import hook (pre-logging) raises ImportError; expect: the error propagates, not swallowed.
- Ask the loader for "now" in UTC and local time; expect: a valid timestamp both ways.

**Configuration**
- Config is read once and cached; expect: repeated access returns the same values.
- Config module named by environment is missing or not a package; expect: error raised in strict mode, silent (or a "not configured" warning, if enabled) in lenient mode.
- Config module name given with a ".py" suffix; expect: treated as invalid, same as not a package.
- Config module exists and is importable, read twice; expect: same settings both times.
- Config module name points to nonexistent module: strict raises ImportError, lenient continues.
- No config found at all; expect: loader reports "not configured" rather than crashing.
- No config environment variable set; expect: reading config returns nothing.
- Config loaded from an object/module; expect: backend overrides in it are picked up.
- Command-line setting with wrong type (e.g. port=foobar); expect: ValueError.

**Importing**
- Import a module from the current directory whether or not cwd is on the path; expect: succeeds both ways.
- Custom importer supplied; expect: it is used.

**Task discovery**
- Autodiscover task modules from several packages; expect: found modules are registered, repeat discovery is guarded against races.
- Package exists, no tasks module; expect: nothing found, no error.
- Package and tasks module both exist; expect: tasks module returned.
- Dotted parent that is not importable but its tasks module is; expect: tasks module still found.
- Neither parent nor tasks module exist (nested); expect: nothing returned.
- Package itself missing; expect: ModuleNotFoundError propagates, naming the package.
- Tasks module exists but imports a missing nested dependency; expect: error propagates with original message, not hidden as "no tasks".
