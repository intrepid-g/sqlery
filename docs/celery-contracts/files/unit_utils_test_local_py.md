### `t/unit/utils/test_local.py`

This file tests Celery's lazy proxy objects (`Proxy`, `PromiseProxy`) and an optional-import helper. Portable guarantees for sqlery: lazy resolution, evaluate-once, transparent delegation, and graceful failure.

**Optional imports**
- Try to load an importable module; expect: it loads successfully.
- Try to load a module that does not exist and give a default; expect: the default is returned, no exception.

**Lazy handle transparency (proxy to a job, queue or config object)**
- Wrap an object in a lazy handle; expect: its name, docstring, class, attribute dict and repr are those of the real object, and a name or docstring can be overridden.
- Wrap an object whose truth value is false; expect: the handle is also false.
- Wrap an object with no attribute dict (restricted slots); expect: asking the handle for its attributes raises an error.
- Wrap an object whose directory listing works; expect: the handle lists the same names.
- Wrap an object whose directory listing raises; expect: the handle lists nothing, no crash.
- Get, set, and delete attributes through the handle; expect: reads and writes reach the real object, and deleting an override restores the original value.
- Use the handle as a dict (set, get, length, contains, delete, iterate); expect: it behaves like the real dict, and a missing key raises a missing-key error.
- Use the handle as a list (append, extend, index, slice, slice-assign, slice-delete, contains, length, string form); expect: it behaves like the real list.
- Use the handle as a number (arithmetic, bit operations, comparisons, in-place operators, abs, float, int, hex, oct); expect: results equal those of the real number, and handles compare equal to handles.
- Hash the handle; expect: the real object's hash.
- Call the handle; expect: the real object is called and its result returned.
- Use the handle as a context manager; expect: enter and exit run on the real object and the entered value is returned.
- Reduce (serialize) the handle; expect: the real object's serialization result is returned.
- Conversion hooks (complex, index, coerce) on the handle; expect: delegated to the real object.
- Handle with a local-storage backing; expect: the current object is fetched from that storage.

**Evaluate-once (lazy promise)**
- Create a lazy promise and read attributes twice; expect: the underlying factory ran exactly once.
- Register several callbacks before evaluation; expect: not yet evaluated, callbacks pending. Then first use; expect: evaluated, every callback called once with the object, pending list gone.
- Register a callback after evaluation; expect: it runs immediately.
- Resolve a promise or a plain value via a "maybe evaluate" helper; expect: a promise yields its value (same value on repeat calls) and a plain value passes through unchanged.

**Import safety**
- Re-import the top-level package after removing it from the module cache; expect: import succeeds and its module spec matches the original lookup.
