# Archived Development Documents

This directory contains historical development planning documents from earlier
phases of the sqlery project. They are kept for reference purposes only.

## Important notes

- These documents may reference **outdated plans, APIs, or architectural
  approaches** that no longer reflect the current state of the project.
- Some files describe migration strategies, step-by-step implementation plans,
  or internal review notes that were relevant during development but are not
  intended as user-facing documentation.
- File names like `STEP1_EXECUTIVE_SUMMARY.md` through
  `STEP8_EXECUTIVE_SUMMARY.md` correspond to sequential development phases
  that have since been completed and consolidated.

## Contents overview

| Category | Files | Description |
|---|---|---|
| Step summaries | `STEP1_*` through `STEP8_*` | Phase-by-phase implementation summaries |
| Migration plans | `DJANGO_MIGRATION_*`, `FASTAPI_MIGRATION_*` | Framework integration planning |
| Architecture | `STANDALONE_PLAN.md`, `PACKAGE_SPLIT_PLAN.md`, `DJANGO_AGNOSTIC_DESIGN.md` | Architectural design documents |
| SQLite/PostgreSQL | `SQLITE_*` | Database compatibility planning |
| Testing | `CHAOS_TESTING_PLAN.md`, `CHAOS_TEST_FINDINGS.md` | Resilience and chaos testing |
| Other | `mvp.plan.md`, `idea.md`, `similar-idea.md`, etc. | Early-stage planning and ideation |

## For contributors

If you are contributing to sqlery, the current documentation in the parent
`docs/` directory and the project README are the authoritative sources. These
archived files may be useful for understanding past design decisions but should
not be treated as current specifications.
