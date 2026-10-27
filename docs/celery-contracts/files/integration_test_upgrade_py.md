### `t/integration/test_upgrade.py`

This file has no job-queue behaviour. It tests Celery's one-off command that rewrites an old settings file. The portable guarantees are about safe, lossless config migration, listed below as sqlery's closest equivalent (a config/settings migration command).

**Settings migration command**
- Run the migration command on a settings file that uses old-style setting names; expect: exit code 0, the message "Changes to your setting have been made!", and old names rewritten to the new names (broker URL and eager-execution flag renamed).
- Run it with the default options; expect: a backup copy of the original file is kept next to it (`.orig`) and the backup is byte-identical to the original.
- Run it with the framework-prefixed style option and no-backup option; expect: names rewritten in the prefixed style (uppercase, with prefix) and no backup file created.

**Preservation and safety**
- Settings file contains Windows line endings (CRLF); expect: line endings preserved exactly after rewrite.
- Settings file contains non-ASCII text in a comment (UTF-8); expect: comment preserved byte-for-byte, no encoding corruption.
- Values and comments that are not setting names are left untouched; expect: only the setting names change.
- Command runs as a real separate process on a real file (not in-process); expect: it works end to end.

**Unexpected problems**
- The command itself must not emit deprecation warnings (they are treated as errors); expect: exit code 0 and no deprecation warning text on stderr.
- On failure, stderr must carry a useful message (the test asserts stderr on a non-zero exit); expect: errors are surfaced, not swallowed.
