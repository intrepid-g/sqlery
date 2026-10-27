### `t/unit/security/test_key.py`

Portable guarantee: a signing key used to authenticate job payloads must be validated on load, bad keys must fail loudly with a clear security error, and unsupported signing options must not silently succeed.

**Loading a signing key**
- Load a valid unencrypted key (two different ones); expect: both load without error.
- Load a valid password-protected key (two different ones) with the correct password; expect: both load without error.

**Rejecting bad keys**
- Load a key with nothing (null); expect: a security error (or a type error), never a usable key.
- Load an empty string; expect: security error.
- Load garbage text ("foo"); expect: security error.
- Load a valid key with one character deleted (corrupted); expect: security error.
- Load a password-protected key with the wrong password (for both keys); expect: security error.
- Load a certificate where a private key is expected; expect: security error.
- Load a key of an unsupported type (ECDSA); expect: security error (only the supported key type is accepted).

**Signing**
- Sign a message with a valid key and the default digest; expect: signature produced, no error.
- Sign with an unknown digest algorithm name; expect: attribute error, no signature produced.
- Signing with an ECDSA key is not supported (test is commented out); expect: not part of the contract.
