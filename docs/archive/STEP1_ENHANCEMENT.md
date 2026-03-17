# Step 1 Enhancement: Auto-Detection of Async Context

**Date**: 2025-11-05
**Requested By**: User
**Status**: 📝 Documented in PLAN (to be implemented in Step 2)

---

## Requirement

The backend factory should **auto-detect** whether it's running in an async context (event loop) or sync context, and create the appropriate backend automatically.

### Current Behavior (Step 1)
```python
# User must explicitly specify backend type
backend = BackendFactory.create_backend(
    'postgresql://localhost/myapp',
    backend_type='sync'  # Must specify
)
```

### Desired Behavior (Step 2+)
```python
# Auto-detects context and creates appropriate backend
backend = BackendFactory.create_backend('postgresql://localhost/myapp')
# → SyncDatabaseBackend if no event loop
# → AsyncDatabaseBackend if inside event loop
```

---

## Implementation Details

### Detection Logic

```python
@staticmethod
def _detect_backend_type() -> BackendType:
    """Detect whether to use sync or async backend based on context.

    Returns:
        'async' if running inside async event loop, 'sync' otherwise
    """
    import asyncio
    try:
        asyncio.get_running_loop()
        return 'async'  # Event loop is running
    except RuntimeError:
        return 'sync'   # No event loop
```

### Updated Factory Method

```python
@classmethod
def create_backend(
    cls,
    connection_string: str,
    backend_type: BackendType | None = None,  # None = auto-detect
    **options: Any
) -> SyncStorageBackend | AsyncStorageBackend:
    """Create a backend instance with optional auto-detection."""

    # Auto-detect if not specified
    if backend_type is None:
        backend_type = cls._detect_backend_type()

    if backend_type == 'sync':
        return SyncDatabaseBackend(connection_string, **options)
    elif backend_type == 'async':
        return AsyncDatabaseBackend(connection_string, **options)
    else:
        raise ValueError(f"Invalid backend_type: {backend_type}")
```

---

## Usage Examples

### Example 1: Sync Context (CLI, Scripts)
```python
from sqlery.backends.factory import BackendFactory

# No event loop → auto-detects sync
backend = BackendFactory.create_backend('postgresql://localhost/myapp')
# Creates: SyncDatabaseBackend
backend.connect()
```

### Example 2: Async Context (FastAPI, AsyncIO)
```python
import asyncio
from sqlery.backends.factory import BackendFactory

async def main():
    # Inside event loop → auto-detects async
    backend = BackendFactory.create_backend('postgresql://localhost/myapp')
    # Creates: AsyncDatabaseBackend
    await backend.connect()

asyncio.run(main())
```

### Example 3: Override Auto-Detection
```python
async def main():
    # Force sync backend even in async context
    backend = BackendFactory.create_backend(
        'postgresql://localhost/myapp',
        backend_type='sync'  # Explicit override
    )
    backend.connect()  # Blocking call
```

---

## Benefits

1. **Developer Experience** - No need to specify backend type manually
2. **Less Boilerplate** - Simpler API for common use cases
3. **Context-Aware** - Automatically adapts to environment
4. **Backward Compatible** - Explicit type still works (optional parameter)
5. **Smart Defaults** - Does the right thing in 90% of cases

---

## Testing Requirements

### Unit Tests to Add
1. **Test auto-detection in sync context**
   ```python
   def test_auto_detect_sync():
       backend = BackendFactory.create_backend('postgresql://localhost/test')
       assert isinstance(backend, SyncDatabaseBackend)
   ```

2. **Test auto-detection in async context**
   ```python
   async def test_auto_detect_async():
       backend = BackendFactory.create_backend('postgresql://localhost/test')
       assert isinstance(backend, AsyncDatabaseBackend)
   ```

3. **Test explicit override in sync context**
   ```python
   def test_explicit_async_in_sync_context():
       backend = BackendFactory.create_backend(
           'postgresql://localhost/test',
           backend_type='async'
       )
       assert isinstance(backend, AsyncDatabaseBackend)
   ```

4. **Test explicit override in async context**
   ```python
   async def test_explicit_sync_in_async_context():
       backend = BackendFactory.create_backend(
           'postgresql://localhost/test',
           backend_type='sync'
       )
       assert isinstance(backend, SyncDatabaseBackend)
   ```

5. **Test default parameter backward compatibility**
   ```python
   def test_backward_compatibility():
       # Old code still works
       backend = BackendFactory.create_backend(
           'postgresql://localhost/test',
           backend_type='sync'
       )
       assert isinstance(backend, SyncDatabaseBackend)
   ```

---

## Implementation Timeline

- **Step 1**: ✅ Documented in STANDALONE_PLAN.md
- **Step 2**: Implement `_detect_backend_type()` method
- **Step 2**: Update `create_backend()` signature to `backend_type: BackendType | None = None`
- **Step 2**: Add auto-detection logic
- **Step 2**: Write 5 new tests for auto-detection
- **Step 3**: Verify async backend works with auto-detection
- **Step 4**: Use auto-detection in Queue/Worker classes

---

## Documentation Updates

### Updated STANDALONE_PLAN.md
- ✅ Section 5.4: Added `_detect_backend_type()` method
- ✅ Section 5.4: Updated `create_backend()` signature
- ✅ Section 5.4: Added auto-detection usage examples
- ✅ Factory Pattern Benefits: Added "Auto-Detection" benefit

### Will Update in Implementation
- README.md: Add auto-detection to quickstart
- API documentation: Document auto-detection behavior
- Migration guide: Show how to use auto-detection

---

## Edge Cases to Consider

1. **Nested Event Loops**
   - What if event loop exists but is not running?
   - Solution: `get_running_loop()` only returns running loops

2. **Threading**
   - Different threads may have different event loops
   - Solution: Detection is per-thread (asyncio is thread-local)

3. **Jupyter Notebooks**
   - Jupyter runs event loop in background
   - Solution: Auto-detection correctly identifies async context

4. **Testing**
   - Tests may or may not run in event loop
   - Solution: Tests can explicitly override with `backend_type` parameter

---

## Impact Analysis

### Code Changes Required
- **backends/factory.py**: ~15 lines added
  - Add `_detect_backend_type()` method
  - Update `create_backend()` signature
  - Add auto-detection logic

### Tests Changes Required
- **tests/backends/test_factory.py**: ~50 lines added
  - 5 new test methods for auto-detection
  - Update existing tests if needed

### Documentation Changes
- **STANDALONE_PLAN.md**: ✅ Already updated
- **README.md**: Update quickstart examples
- **API docs**: Document auto-detection

### Breaking Changes
- **None**: Fully backward compatible
  - Existing code with explicit `backend_type` still works
  - New code can omit `backend_type` for auto-detection

---

## Alignment with Design Principles

✅ **Principle of Least Surprise** - Auto-detects the obvious choice
✅ **Convention over Configuration** - Smart defaults reduce boilerplate
✅ **Explicit over Implicit** - Can still specify explicitly if needed
✅ **Developer Experience** - Simpler API for common use cases

---

## Conclusion

This enhancement aligns perfectly with the standalone-first design philosophy and significantly improves the developer experience. It will be implemented as part of Step 2 when the factory code is being actively worked on.

**Status**: Ready for implementation in Step 2
**Priority**: High (user-requested feature)
**Risk**: Low (backward compatible, well-defined logic)
