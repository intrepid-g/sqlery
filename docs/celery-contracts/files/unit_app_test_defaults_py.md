### `t/unit/app/test_defaults.py`

Portable guarantee: the built-in default configuration is internally consistent, and legacy and new setting names map to each other without gaps. No job-processing behaviour is tested here.

**Settings metadata**
- Print the description of any setting; expect: a non-empty readable representation.
- Run an "any"-typed setting through type conversion; expect: the exact same value back, unchanged.

**Legacy and new setting names**
- Check every default setting key; expect: all in the new (lowercase) style, none uppercase.
- Check every legacy default key; expect: all uppercase, none lowercase.
- Check the new-to-old and old-to-new name maps; expect: keys and values are each in the right case, with no mixing.
- Check every key in the new-to-old map; expect: it is a known legacy setting name.
- Check every key in the old-to-new map; expect: it is a known current setting name. No name maps to something that does not exist.

**Lookup**
- Look up a setting by its short name (default queue); expect: found, with default "celery".
- Look up a setting whose default is unset (default exchange); expect: found, default is none (not an error).

**Worker defaults**
- Read the worker pool start method default, both from the namespace and from the flat defaults; expect: "fork" in both, and the two agree.

**Isolation**
- Reload the defaults module for each test and restore the original afterwards; expect: no state leaks between tests.
