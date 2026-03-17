# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [0.11.0] - Unreleased

### Added

- Unified v0.11.0 architecture with core modules and admin (`e8a968c`)
- Worker management CLI commands (`5ba550a`)
- Chaos testing infrastructure (`0940a4b`)
- Database indexes for hot query paths (`9036b47`)
- Comprehensive documentation for package split and configuration (`52a5bc0`)

### Fixed

- SQLite compatibility and v0.11.0 CLI wiring (`4f37195`)
- MySQL atomic upsert for worker heartbeat race condition (`61d27e0`)
- Race conditions in job claiming and worker heartbeat for SQLite and PostgreSQL (`6e66894`)
- Package split import errors and auto-trigger implementation (`301ee18`)
- Replace `wraps(func)(self)` with `update_wrapper` (`4476f7c`)
- `update_scheduled_task()` for standalone mode (`bc36a72`)

### Changed

- Rename `fastapi/` to `fastapi_sqlery/` for package consistency (`d7e8b39`)
- Rename `django/` to `django_sqlery/` for package consistency (`9b0cbbf`)
- Move Django code to `django/` subfolder (`feef24e`)
- Integrate Django and FastAPI package split (`53d9adb`)

### Tests

- Update tests for v0.11.0 and fix fake test patterns (`3a95f23`)
- Achieve 100% SQLite + PostgreSQL compatibility (49/49 tests passing) (`d2842ce`)

### Chores

- Organize documentation files (`6293bca`)
- Remove `__pycache__` from git tracking (`4c43c06`)

## [3.0.0] - Initial Release

- Initial commit: sqlery v3.0 - standalone job queue + cron scheduling (`f78f7ff`)
