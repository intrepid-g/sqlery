### `t/unit/utils/test_iso8601.py`

Portable guarantee: timestamps stored or passed as ISO 8601 strings (e.g. scheduled run times, ETAs, expiries) are parsed exactly, and bad input fails loudly.

**Timezones**
- Parse a timestamp ending in `Z`; expect the exact instant, tagged UTC.
- Parse a timestamp with a positive offset (+05:30); expect the same wall time with that offset preserved.
- Parse a timestamp with a negative offset (-08:00); expect the offset preserved.

**Precision**
- Parse a timestamp with fractional seconds (.123456); expect microseconds preserved exactly.

**Partial input**
- Parse a date only ("2023-10-26"); expect midnight, with no timezone (naive).
- Parse date plus hour:minute only (no seconds); expect seconds = 0, naive.

**Bad input**
- Parse a string that is not a date at all; expect a clear "unable to parse date string" error, not a silent default.
- Parse well-formed strings with impossible values (month 13, day 32, hour 25, minute 60, second 60); expect each to be rejected with an error.
- Parse a timestamp with a garbled offset (+05:AA); current behaviour: the offset is silently ignored and a naive time is returned (a quirk, not a recommended guarantee; sqlery should reject or flag this).
- Parse the compact form without separators ("20231026T103000Z"); current behaviour: crashes with a type error rather than a clean parse error (sqlery should raise a clear error instead).

**Deprecation**
- Call the parser; expect a pending-deprecation warning saying it is scheduled for deprecation (old helpers warn but keep working).
