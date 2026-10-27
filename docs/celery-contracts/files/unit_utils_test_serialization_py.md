### `t/unit/utils/test_serialization.py`

**Making job payloads/results safe to store**
- Store a job result containing a value the serializer cannot encode (e.g. a class object); expect: the value is replaced by its text form (`"<class 'object'>"`) and the rest (`1`) is kept, with no error.
- Store a result using a serializer that can handle the value (pickle); expect: the value round-trips unchanged.

**Failed jobs whose exception cannot be stored**
- A job raises an exception with arguments that cannot be serialized (e.g. a function); expect: the failure is still recorded, wrapped with the exception class name, module and arguments preserved (2 args kept).
- A job raises an exception class whose own serialization raises; expect: the recorded exception type falls back to the generic base Exception, and the job is marked failed (not crashed).

**Converting values to JSON-safe form (job args, results, dashboard)**
- Convert queue-like objects, lists, dicts, ints, floats, strings; expect: a non-empty JSON-safe value for each.
- Convert datetimes (UTC, zone-aware, no microseconds), dates, times (with/without microseconds) and durations; expect: each converts without error.
- Convert an unknown object type with a custom fallback handler; expect: the handler is called with the object and its return value is used.
- Convert an unknown object type with no fallback; expect: a ValueError, not silent corruption.

**Parsing boolean settings**
- Parse each standard true/false word from the default table; expect: the mapped boolean.
- Parse an unrecognized word ("foo"); expect: a clear error "Cannot coerce 'foo' to type bool".
- Pass a value that is already non-string (1); expect: returned unchanged.
- Parse with a custom table; expect: custom words honoured.

**Environment fallback**
- Run where the fast C pickle module is unavailable; expect: the standard pickle is used transparently.
