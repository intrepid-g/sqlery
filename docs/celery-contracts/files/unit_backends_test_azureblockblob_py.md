### `t/unit/backends/test_azureblockblob.py`

Portable guarantee: a result store (Azure blob vendor wiring aside) must store, fetch, delete and expire results safely, fail clearly on bad config, never leak secrets, and survive pickling.

**Configuration and startup**
- Start the result store when the vendor SDK is not installed; expect: a clear configuration error, not an obscure crash.
- Start with an empty or malformed connection URL; expect: a configuration error.
- Access the storage client for the first time; expect: the container is created then, not at construction, and only once per store instance.
- Connect with a default-credential or managed-identity URL; expect: credentials are resolved from the URL and the client is built with the configured timeouts.
- Set connection and read timeouts in config; expect: they are passed to the client.
- Set a base path (empty or a folder prefix) in config; expect: it is used as given; with no setting, expect: empty default.

**Store, fetch, delete**
- Store a job result under a key; expect: it lands at base path + key and overwrites any previous value.
- Fetch a result; expect: the stored value is returned decoded.
- Fetch a result that does not exist (expired, forgotten, never stored); expect: None, no exception.
- Fetch several results at once; expect: one lookup per key, each under base path + key, in any order.
- Delete a result; expect: it is removed from base path + key.
- Delete a result that is missing (expired, never stored); expect: silent no-op, no error.
- Delete the same result twice; expect: first removes it, second is a no-op.

**Serialization and workers**
- Pickle and unpickle the store (container name, expiry, connection string); expect: all settings preserved.
- A job returns a handle to another result, serialized with pickle; expect: the handle decodes and its store keeps the same connection settings, so the result is readable.

**Secret redaction when displaying the store URL**
- Show URL with password allowed; expect: full URL unchanged.
- Show URL without password; expect: account key replaced with `**`, other fields intact.
- Redact shared access signatures, including ones starting with `?`; expect: nothing of the token remains.
- Redact the `sig` parameter inside a blob endpoint URL, case-insensitively (`sig`, `SIG`); expect: replaced with `**`, other query parameters (version, comp) and the endpoint itself preserved.
- Redact keys case-insensitively (`accountkey`); expect: redacted.
- Redact an account key containing `#`; expect: the whole value redacted, no fragment leaked.
