### `t/unit/app/test_celery.py`

Package-level metadata only; no queue behaviour. Portable guarantees for sqlery:

**Version**
- Import the package; expect a non-empty version tuple with at least 3 parts (major, minor, patch).
- Overwrite the version tuple at runtime; expect the version string to be derived from it and still contain at least 2 dots.

**Package metadata**
- Import the package; expect author, contact, homepage and docformat attributes all to exist and be non-empty.
