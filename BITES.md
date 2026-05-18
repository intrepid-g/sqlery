## 2025-05-18 — Fix worker claim race condition

**Built:** Auto-register workers on-demand when claiming jobs if worker row not found in database, preventing "workers idle but jobs waiting" scenario.

**Decisions:**
- Race condition handling: chose immediate auto-registration, alternative was retry loop with exponential backoff
- Worker validation: chose trust-on-first-claim (any worker ID format is accepted), alternative was whitelist/shared secret validation

**Deferred:**
- Add retry loop with exponential backoff before auto-registration
- Add worker registration security (whitelist or shared secret)
