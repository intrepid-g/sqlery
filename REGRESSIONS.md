# Regressions

Tracks bugs that have reappeared or were caught during testing.

## Format

```
### YYYY-MM-DD — Short description
- **Symptom:** What broke
- **Root cause:** Why it happened
- **Fix:** How it was resolved
- **Commit/PR:** Reference
```

---

## History

### 2026-03-10 — ORM errors and stress-test regressions (efd535a)
- **Symptom:** Stress test pass rate dropped below 500/500; ORM query errors in certain backends
- **Root cause:** Backend method signature mismatches after refactor
- **Fix:** Resolved ORM errors and corrected backend calls
- **Commit:** efd535a / 944b8c5
