### `t/unit/bin/test_upgrade.py`

Portable guarantee: a config-migration tool rewrites old setting names to new ones safely, and does nothing when nothing needs changing.

**Rename and backup**
- Given a settings file with old-style keys (broker URL, always-eager flag) and a comment with non-ASCII text, run the upgrade; expect: exit code 0, a "changes have been made" message, keys renamed to the new names.
- Same run; expect: Windows line endings (CRLF) and non-ASCII bytes left exactly as they were, only key names change.
- Same run with backup enabled (default); expect: a `.orig` copy of the file holding the exact original bytes.

**Alternate naming scheme**
- Run the upgrade with the Django flag and no-backup; expect: keys renamed with the framework prefix (e.g. `CELERY_BROKER_URL`), line endings and non-ASCII text untouched, and no `.orig` backup created.

**Idempotence / no-op**
- Run the upgrade on a file already using new key names; expect: exit code 0, message "does not seem to require any changes", file bytes unchanged, no backup file created.

**Test isolation**
- Process environment variables are cleared and restored around each run, since the CLI mutates them.
