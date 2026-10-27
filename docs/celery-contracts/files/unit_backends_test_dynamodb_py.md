### `t/unit/backends/test_dynamodb.py`

Portable guarantee: a vendor result store (DynamoDB) must store, fetch, delete and expire results, validate its configuration up front, and surface storage errors instead of hiding them.

**Configuration and startup**
- Start the result store without its required client library; expect a clear configuration error.
- Give credentials with a user but no secret (`a:@`); expect a configuration error.
- Give a non-numeric expiry (`ttl_seconds=1d`); expect a value error.
- Point at a custom endpoint via setting; expect the client uses that endpoint and region.
- Point at localhost or 127.0.0.1 with a port; expect a plain local http endpoint and a default region.
- Give a key and secret in the URL; expect them passed to the client with the named region.
- Parse URL params (region, table name, read/write capacity, expiry seconds); expect each exposed as a setting, with no endpoint by default.
- Resolve a `dynamodb://` URL; expect the DynamoDB store is chosen.
- Set an expiry and create the client; expect expiry support is validated and the table expiry is configured.

**Table lifecycle**
- Table missing on startup; expect it is created from the schema, then waited on until active.
- Table already exists; expect no second creation.
- Table still CREATING; expect polling until the expected status appears.

**Expiry (retention)**
- No expiry configured: unset. Negative: disabled. Zero or positive: enabled.
- Client lacks the expiry methods; expect an error, every time it is checked.
- Enable expiry while disabled; expect the table is updated once.
- Enable expiry while already enabled on the correct field; expect no error and no change.
- Enable expiry while it is being disabled, or enabled on a different field; expect the storage error is raised, not swallowed.
- Disable expiry while already disabled; expect success with no change.
- Disable expiry while it is being enabled; expect the storage error is raised.
- Checking expiry status fails with a storage error; expect it is raised.

**Storing and reading results**
- Store a result; expect an item with id, result payload and timestamp, and nothing returned.
- Store a result with expiry; expect the item also carries an expiry time of timestamp plus seconds (whole number).
- Fetch a result by id; expect the lookup is by that id only, and a missing result gives nothing.
- Delete a result by id; expect it is removed by id and nothing returned.
- Convert a raw stored item; expect typed values unwrapped to plain id, result, timestamp.

**Dependencies (chord-like counters)**
- Start a group with a completion callback (3 jobs); expect a counter record at 0 and a stored group-metadata record, written in that order.
- Mark one member done; expect the counter is incremented atomically (single increment of 1) and the new count is returned.
