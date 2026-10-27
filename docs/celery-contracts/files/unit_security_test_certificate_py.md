### `t/unit/security/test_certificate.py`

Portable guarantee: signing credentials (certificates) are validated on load, expired ones are rejected, and a store of them refuses duplicates and bad input. For sqlery this maps to validating signing keys/certs used to authenticate job payloads, if ever adopted.

**Loading a credential**
- Load two valid certificates; expect both accepted.
- Load nothing (None); expect a rejection (security error or type error).
- Load an empty string; expect a security error.
- Load garbage text ("foo"); expect a security error.
- Load a valid certificate with one character removed (corrupted); expect a security error.
- Load a private key where a certificate is expected; expect a security error.
- Load a certificate using an unsupported key type (ECDSA); expect a security error.

**Expiry**
- Certificate whose end date was yesterday; expect reported as expired.
- Certificate whose end date is tomorrow; expect reported as not expired.
- Verify a signed payload with a non-expired certificate; expect success.
- Verify the same signed payload when the certificate is expired; expect a security error "Expired certificate", even though the signature is good.

**Certificate store (in memory)**
- Empty store; expect iterating yields nothing.
- Add 2 certificates; expect iteration yields exactly those 2.
- Add the same certificate twice; expect a security error on the second add.

**Certificate store (directory on disk)**
- Directory with one valid, unexpired certificate file; expect it is loaded and findable by its id, and every file in the directory is scanned.
- Directory with two files whose certificates share the same id; expect a security error (duplicate).
- Directory whose certificate is expired; expect a security error at load time.
- Path that is not a directory; expect a security error.
