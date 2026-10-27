### `t/unit/security/test_serialization.py`

Signed payloads: a message is signed with a private key and verified against a trusted certificate store (certificate expiry is bypassed in these tests).

**Round trips**
- Sign then verify an int, a string, raw bytes and a dict, with both JSON and pickle encodings; expect the original value back, identical.
- Sign then verify a dict whose value is the signature separator string itself; expect it comes back intact (payload content cannot confuse the framing).
- Sign and verify 1000 different random payloads, building a fresh signer each time; expect every one verifies and equals the original (no flakiness or state leakage).

**Rejecting bad input**
- Verify a string that was never signed ("bad data"); expect a security error, not a crash or a silent accept.
- Signer's private key does not match its own certificate (key 1, cert 2); expect verification fails with a security error.
- Message signed by a sender whose certificate is not in the receiver's trust store; expect a security error.
- Receiver has an empty trust store; expect a security error.

**Trust topologies**
- A node sends to itself (own cert trusted); expect the message verifies.
- Two nodes with different keys, each trusting only the other's certificate; expect A's message verifies at B.

**Setup**
- Registering the signed-message format installs a decoder for the `application/data` content type, so signed messages are recognised on receipt.
