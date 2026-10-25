---
status: in-progress
quick_id: 260814-fp7
slug: dashboard-dark-theme-compatibility
date: 2026-08-14
branch: fix/dashboard-dark-theme

task: >
  Make both sqlery dashboards readable under a dark theme.

success_criteria:
  - No hardcoded light-only colour survives outside a theme-token block
  - Django admin dashboard follows the admin theme (auto / light / dark)
  - FastAPI dashboard responds to prefers-color-scheme
  - One runnable test guards the rule

not_needed_when:
  - dark theme shipped and merged

summarize_when:
  - implementation complete
---

# Dashboard dark-theme compatibility

## Problem

**Django admin dashboard.** `src/sqlery/django_sqlery/static/sqlery/css/dashboard.css`
and the admin templates mix two styles. Most rules read Django admin CSS
variables (`--body-bg`, `--body-fg`, `--border-color`, `--darkened-bg`), which
already follow the admin theme. A minority hardcode light-only colours with no
variable at all — `#fffbe6`, `#fff8f0`, `#fff3cd`, `#f8f9fa`, `#ddd`, `#fff`,
`#f5f5f5`, `#f4f4f4`, `#eee`. Under the admin dark theme those paint a near-white
background while the text colour still comes from `--body-fg` (near-white).
Result: white on white.

**FastAPI standalone dashboard.** `src/sqlery/fastapi_sqlery/templates/*.html`
use the Tailwind CDN with light-only utilities (`bg-gray-50`, `bg-white`,
`text-gray-900`, `border-gray-200`, `bg-blue-50`, …) and no `dark:` variants at
all. In a dark browser/OS the page stays fully light, and any surrounding
chrome clashes.

## Approach

Two independent tracks — one per integration mode.

### Track A — Django admin

1. Add a theme-token block at the top of `dashboard.css` that mirrors Django
   admin's own three-state theme switcher:
   - `:root` — light values
   - `@media (prefers-color-scheme: dark)` guarded by
     `html[data-theme="dark"], html:not([data-theme="light"])` so the admin
     "auto" setting works and the explicit light choice still wins
   - `html[data-theme="dark"]` — so the admin toggle wins in both directions
   Tokens cover what admin variables do not: highlight/attention surfaces
   (`--sqlery-highlight-bg`, `--sqlery-warn-bg`, `--sqlery-warn-fg`) and a
   neutral surface/border pair for the templates.
2. Replace every hardcoded light hex in `dashboard.css` and in the admin
   templates' inline styles and `<style>` blocks with either an existing Django
   admin variable or one of the new tokens.
3. Keep every existing `var(--x, fallback)` fallback intact — those are correct.

Files: `src/sqlery/django_sqlery/static/sqlery/css/dashboard.css`,
`src/sqlery/django_sqlery/templates/admin/sqlery/*.html`,
`src/sqlery/templates/admin/sqlery/*.html` (older duplicate copies — theme them
too rather than risk breaking an install that still resolves them).

### Track B — FastAPI standalone

1. In `base.html`, set an explicit Tailwind config (`darkMode: 'media'`) and
   give `<body>` an explicit dark background so the page never inherits the
   host's light ground.
2. Add `dark:` variants to every colour utility across all templates in
   `src/sqlery/fastapi_sqlery/templates/`. Structure and layout classes stay
   untouched — this is a colour-only pass.
3. Status/severity colours (green/red/amber pills) get dark-mode pairs with
   enough contrast, not just the same light chip on a dark card.

### Track C — guard

One pytest file, `tests/test_dashboard_dark_theme.py`, that reads the static
files and asserts:
- No light-only hex literal appears in `dashboard.css` outside the token block.
- Every FastAPI template colour utility that sets a light background or a dark
  text colour has a matching `dark:` variant on the same element.

The test fails if someone re-adds a light-only colour, which is exactly the
regression this task fixes.

## Out of scope

- No visual redesign. Colours only.
- No new dependency, no build step, no theme-toggle UI.
- The `src/sqlery/templates/` vs `src/sqlery/django_sqlery/templates/`
  duplication is left in place; it is a separate cleanup.
