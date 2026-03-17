# Chaos Testing Findings

## Summary

Chaos testing revealed critical issues with the package split and uncovered potential failure modes.

---

## 🔥 Critical Issue Found: Broken Core Imports

**Issue**: Package split moved `queue.py` to `django_sqlery/` but left it as a commented stub. However, the core `__init__.py` still tries to import `Queue` from it.

**Error**:
```
ImportError: cannot import name 'Queue' from 'sqlery.queue' (/home/sandbox/workspace/sqlery/src/sqlery/queue.py)
```

**Root Cause**:
- `src/sqlery/__init__.py` line 63: `from .queue import Queue`
- `src/sqlery/queue.py` is now just commented code (stub for Django migration)
- The `Queue` class was Django-specific, not core

**Impact**:
- ❌ Core sqlery imports broken
- ❌ Can't use `from sqlery import Queue`
- ❌ Tests fail to run
- ❌ Package split broke existing API

**Fix Options**:

### Option 1: Revert queue.py (Quick Fix)
Uncomment the original `queue.py` code. Keep Django and standalone as separate implementations.

### Option 2: Split Correctly (Proper Fix)
- Identify truly *core* functionality (framework-agnostic)
- Keep core classes in `src/sqlery/`
- Move only Django-specific implementations to `django_sqlery/`
- Move only FastAPI-specific implementations to `fastapi_sqlery/`

### Option 3: Remove from Core __init__.py
- Don't export Django-specific classes from core `sqlery` package
- Users should import directly: `from sqlery.django_sqlery import Queue`
- Breaking change but cleaner architecture

**Recommendation**: Option 2 - The `Queue` class appears to be Django-specific. The core package should not export it. The `__init__.py` needs to be updated to only export truly framework-agnostic code.

---

## 🎯 What Chaos Testing Revealed

### Tests Created

1. **Property-Based Tests** (`tests/chaos/test_property_based.py`)
   - Serialization round-trip with random data
   - Job creation with fuzzy inputs
   - Edge cases: long strings, unicode, negative numbers
   - Concurrent job creation
   - Cron expression fuzzing

2. **Worker Chaos Tests** (`tests/chaos/test_worker_chaos.py`)
   - Worker killed mid-job (SIGKILL)
   - Graceful shutdown (SIGTERM)
   - Multiple workers claiming same job (race conditions)
   - Database failures during execution
   - Slow database queries
   - Memory exhaustion
   - Connection pool exhaustion
   - State corruption (invalid status, missing task paths)

### Tests Status

❌ **Cannot run yet** due to import issue above

### Expected Findings (Once Tests Run)

Based on test design, likely to find:

1. **Race Conditions**
   - Multiple workers claiming same job
   - Scheduled jobs created multiple times
   - State transitions during concurrent updates

2. **Orphaned Jobs**
   - Jobs stuck in "running" after worker crash
   - Detection and recovery mechanisms
   - Heartbeat timeout handling

3. **Input Validation Gaps**
   - Extremely long strings
   - Special characters (null bytes, control chars)
   - Negative or zero timeouts
   - Invalid cron expressions

4. **Resource Exhaustion**
   - Memory leaks in long-running workers
   - Connection pool exhaustion under load
   - Database deadlocks

5. **State Corruption**
   - Invalid status values
   - Missing required fields
   - Partial database updates

---

## 📊 Testing Infrastructure Added

### Dependencies
- ✅ `hypothesis>=6.92.0` - Property-based testing
- ✅ `pytest-timeout>=2.2.0` - Prevent hanging tests

### Test Structure
```
tests/chaos/
├── __init__.py
├── test_property_based.py    (100+ examples per test)
├── test_worker_chaos.py        (SIGKILL, SIGTERM, races)
└── (future: test_input_fuzzing.py, test_load.py)
```

### Test Strategies
1. Property-based: Use Hypothesis to generate random inputs
2. Controlled chaos: Deliberately kill processes, corrupt state
3. Race conditions: Run operations concurrently
4. Edge cases: Boundary values, invalid inputs

---

## 🚧 Blocked

**Status**: Cannot proceed with chaos testing until core import issue is fixed

**Next Steps**:
1. Fix broken imports from package split
2. Run property-based tests
3. Run worker chaos tests
4. Document actual failures found
5. Fix critical issues
6. Re-run tests to verify fixes

---

## 💡 Lessons Learned

### Package Split Issues

**Problem**: The package split was done too aggressively:
- Moved Django-specific code
- But didn't distinguish between "Django ORM" and "core functionality used by Django"
- Result: Broke core package imports

**What Should Have Been Done**:
1. Identify *truly* framework-agnostic code (stays in core)
2. Identify Django ORM/integration code (moves to django_sqlery)
3. Identify FastAPI-specific code (moves to fastapi_sqlery)
4. Test imports after each move
5. Run existing test suite to catch breakage

**Root Cause**: The migration script blindly moved files without understanding dependencies and import paths.

### Testing Reveals Architecture Issues

**Key Insight**: Chaos testing immediately revealed that the package split broke fundamental imports. This demonstrates the value of:
- Running tests frequently during refactoring
- Having comprehensive test coverage
- Testing edge cases and failure modes

**Contrarian Win**: By trying to break things, we found a critical issue before users did.

---

## 🔧 Recommended Fixes (Priority Order)

### 1. Fix Import Breakage (CRITICAL - P0)
- Audit `src/sqlery/__init__.py` exports
- Remove Django-specific imports from core
- Update tests to use correct import paths
- **Owner**: TBD
- **Est**: 1-2 hours

### 2. Run Chaos Tests (HIGH - P1)
- After imports fixed, run all chaos tests
- Document actual failures found
- **Owner**: TBD
- **Est**: 2-3 hours

### 3. Fix Critical Bugs Found (HIGH - P1)
- Based on chaos test results
- Prioritize: data loss, crashes, race conditions
- **Owner**: TBD
- **Est**: TBD (depends on findings)

### 4. Add More Chaos Tests (MEDIUM - P2)
- Input fuzzing with malicious payloads
- Load testing (1000s of jobs)
- Network failures (distributed scenarios)
- **Owner**: TBD
- **Est**: 4-6 hours

### 5. Document Failure Modes (MEDIUM - P2)
- Create runbook for common failures
- Document recovery procedures
- Add monitoring/alerting recommendations
- **Owner**: TBD
- **Est**: 2-3 hours

---

**Date**: 2025-11-12
**Status**: Blocked on import issue
**Next Review**: After imports fixed and tests run
