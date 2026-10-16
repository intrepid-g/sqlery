---
status: complete
quick_id: 260814-fp7
slug: dashboard-dark-theme-compatibility
date: 2026-08-14
branch: fix/dashboard-dark-theme

task: >
  Make both sqlery dashboards readable under a dark theme.

not_needed_when:
  - PR merged

summarize_when:
  - already summarised
---

# Dashboard dark-theme compatibility — summary

## What shipped

**Django admin dashboard.** `dashboard.css` now declares `--sqlery-*` theme
tokens on a bare `:root`, overrides them under
`@media (prefers-color-scheme: dark)` guarded by
`html[data-theme="dark"], html:not([data-theme="light"])`, and overrides them
again under `html[data-theme="dark"]`. That mirrors Django admin's own
three-state switcher, so the dashboard follows the admin's light / dark / auto
setting instead of fighting it. Every hardcoded light background in the CSS and
in the admin templates' `<style>` blocks and inline styles now resolves through
a Django admin variable or a `--sqlery-*` token, and every themed background
carries a themed foreground.

Also fixed while in there: `.stat-card` declared `border-left` before the
`border` shorthand, so the accent stripe was silently overwritten.

**FastAPI standalone dashboard.** `base.html` configures the Tailwind CDN with
`darkMode: 'media'`. All eight templates gained `dark:` variants beside every
colour utility — surfaces, text, borders, dividers, hover states, status pills,
`<pre>` traceback blocks, the mobile menu, and pagination.

**Guard.** `tests/test_dashboard_dark_theme.py` (120 cases) asserts the two
rules that actually matter:
1. A Django rule block may not paint a light literal background without setting
   an explicit foreground — that combination is the white-on-white bug.
2. Every light-only Tailwind utility in the FastAPI templates has a `dark:`
   counterpart on the same element, and `base.html` still enables media dark
   mode (without which every `dark:` variant is inert).

Both rules were mutation-checked: removing a `dark:` variant and restoring the
old `#fffbe6` highlight each make the suite fail.

## Decisions

- **Auto only, no toggle.** Both dashboards follow `prefers-color-scheme`; the
  Django one additionally respects the admin's own theme switcher. No new UI,
  no persistence, no JS, no dependency.
- **Self-pairing pastel badges left alone.** `#d1ecf1`/`#0c5460`,
  `#d4edda`/`#155724`, `#f8d7da`/`#721c24` and friends set their own background
  *and* foreground, so they stay legible in either theme. Only the warning pair
  (`#fff3cd`/`#856404`) became a token, because it also drives non-badge
  surfaces.
- **Tokens duplicated into three `<style>` blocks.** `dashboard.css` is only
  `<link>`ed from `unified_dashboard.html`. Referencing `var(--sqlery-warn-bg)`
  from `dashboard.html`, `change_list.html` or `scheduledtask/change_form.html`
  would have silently fallen back to the light literal forever. Adding a
  stylesheet link to each was a structural change beyond this task's scope, so
  the token block is repeated locally in the three templates that need it.

## Known follow-ups

- `src/sqlery/templates/admin/sqlery/` duplicates
  `src/sqlery/django_sqlery/templates/admin/sqlery/` with *diverged* content.
  Nothing under `src/` references the former. Both copies were themed here to
  avoid guessing, but the duplication deserves its own cleanup pass.
- The three duplicated token blocks collapse into one the day those templates
  load `dashboard.css`.
- No browser screenshot was taken. Verification is static analysis plus the
  existing admin/API suites, not a visual check.
