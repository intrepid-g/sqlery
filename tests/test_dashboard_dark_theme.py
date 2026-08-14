"""Guard the dashboards against light-only colours that break dark themes.

Two rules, one per integration mode:

* Django admin: a rule block must not paint a light background with a literal
  colour unless it also sets an explicit foreground. A light literal is fixed;
  the inherited foreground is not, so under the admin dark theme the text turns
  near-white on a near-white surface.
* FastAPI standalone: the Tailwind CDN runs in ``darkMode: 'media'``. A
  light-only colour utility with no ``dark:`` counterpart on the same element
  keeps that element light no matter the browser theme.
"""

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "sqlery"

DJANGO_CSS = SRC / "django_sqlery" / "static" / "sqlery" / "css" / "dashboard.css"
DJANGO_TEMPLATE_DIRS = [
    SRC / "django_sqlery" / "templates" / "admin" / "sqlery",
    SRC / "templates" / "admin" / "sqlery",
]
FASTAPI_TEMPLATES = SRC / "fastapi_sqlery" / "templates"

# Above this relative luminance a background needs an explicit foreground.
LIGHT_LUMINANCE = 0.70

NAMED_LIGHT = {"white", "whitesmoke", "ivory", "snow", "azure", "beige"}

RULE_BLOCK_RE = re.compile(r"([^{}]*)\{([^{}]*)\}", re.MULTILINE)
INLINE_STYLE_RE = re.compile(r'style="([^"]*)"')
STYLE_TAG_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.DOTALL | re.IGNORECASE)
VAR_CALL_RE = re.compile(r"var\([^()]*(?:\([^()]*\)[^()]*)*\)")
HEX_RE = re.compile(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b")


def _relative_luminance(hex_colour: str) -> float:
    """WCAG relative luminance of a 3- or 6-digit hex colour."""
    digits = hex_colour.lstrip("#")
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    channels = [int(digits[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _declarations(body: str) -> list[tuple[str, str]]:
    """Split a declaration block into (property, value) pairs."""
    pairs = []
    for chunk in body.split(";"):
        prop, sep, value = chunk.partition(":")
        if sep:
            pairs.append((prop.strip().lower(), value.strip()))
    return pairs


def _literal_light_background(value: str) -> str | None:
    """Return the light literal a background value hardcodes, if any.

    Colours reached through ``var()`` are theme-aware by construction, so the
    fallback inside the parentheses does not count.
    """
    without_vars = VAR_CALL_RE.sub("", value).lower()
    for match in HEX_RE.finditer(without_vars):
        if _relative_luminance(match.group(0)) > LIGHT_LUMINANCE:
            return match.group(0)
    for name in NAMED_LIGHT:
        if re.search(rf"\b{name}\b", without_vars):
            return name
    return None


def _light_backgrounds_without_foreground(css: str) -> list[str]:
    """Rule blocks painting a light literal background and setting no colour."""
    offenders = []
    for selector, body in RULE_BLOCK_RE.findall(css):
        decls = _declarations(body)
        if any(prop.startswith("--") for prop, _ in decls):
            continue  # theme-token declaration block
        sets_foreground = any(prop == "color" for prop, _ in decls)
        for prop, value in decls:
            if prop not in ("background", "background-color"):
                continue
            literal = _literal_light_background(value)
            if literal and not sets_foreground:
                offenders.append(f"{selector.strip()} {{ {prop}: {value} }}  <- {literal}")
    return offenders


def _django_style_sources() -> list[tuple[str, str]]:
    """(label, css) for every stylesheet, <style> block and inline style."""
    sources = [(str(DJANGO_CSS), DJANGO_CSS.read_text())]
    for directory in DJANGO_TEMPLATE_DIRS:
        for template in sorted(directory.rglob("*.html")):
            text = template.read_text()
            for index, block in enumerate(STYLE_TAG_RE.findall(text)):
                sources.append((f"{template}:<style>[{index}]", block))
            for index, block in enumerate(INLINE_STYLE_RE.findall(text)):
                # Wrap so the shared rule-block parser sees a declaration block.
                sources.append((f"{template}:style-attr[{index}]", "inline {" + block + "}"))
    return sources


_DJANGO_STYLE_SOURCES = _django_style_sources()


@pytest.mark.parametrize(
    "label,css",
    _DJANGO_STYLE_SOURCES,
    ids=[label.replace(str(SRC) + "/", "") for label, _ in _DJANGO_STYLE_SOURCES],
)
def test_django_admin_light_background_always_sets_a_foreground(label, css):
    offenders = _light_backgrounds_without_foreground(css)
    assert not offenders, (
        f"{label} hardcodes a light background but inherits its text colour. "
        "Under the Django admin dark theme that is near-white on near-white. "
        "Use a Django admin variable or a --sqlery-* token, or set an explicit "
        "color in the same block.\n  " + "\n  ".join(offenders)
    )


# Light-only Tailwind utilities and the dark: variant each one needs beside it.
# A bare utility with no dark: partner stays light in a dark browser.
TAILWIND_LIGHT_UTILITIES = re.compile(
    r"\b(?:bg-white"
    r"|bg-(?:gray|slate|zinc|neutral)-(?:50|100|200)"
    r"|bg-(?:red|green|blue|yellow|amber|purple|indigo)-(?:50|100)"
    r"|text-(?:gray|slate|zinc|neutral)-(?:500|600|700|800|900)"
    r"|text-(?:red|green|blue|yellow|amber|purple|indigo)-(?:600|700|800|900)"
    r"|border-(?:gray|slate|zinc|neutral)-(?:200|300)"
    r"|divide-(?:gray|slate|zinc|neutral)-(?:200|300)"
    r")\b"
)

CLASS_ATTR_RE = re.compile(r'class="([^"]*)"', re.DOTALL)


def _unpaired_light_utilities(markup: str) -> list[str]:
    """Class attributes with a light-only utility and no dark: variant."""
    offenders = []
    for classes in CLASS_ATTR_RE.findall(markup):
        # Jinja conditionals put both branches in one attribute; that is fine,
        # every branch still has to carry its own dark: variant.
        if not TAILWIND_LIGHT_UTILITIES.search(classes):
            continue
        if "dark:" not in classes:
            flat = " ".join(classes.split())
            offenders.append(flat[:160])
    return offenders


@pytest.mark.parametrize(
    "template",
    sorted(FASTAPI_TEMPLATES.glob("*.html")),
    ids=lambda p: p.name,
)
def test_fastapi_light_utilities_have_dark_variants(template):
    offenders = _unpaired_light_utilities(template.read_text())
    assert not offenders, (
        f"{template.name} uses light-only Tailwind colours with no dark: variant. "
        "The dashboard runs the CDN in darkMode: 'media', so these stay light in "
        "a dark browser.\n  " + "\n  ".join(offenders)
    )


def test_fastapi_base_template_enables_media_dark_mode():
    """The dark: variants above are inert without this config."""
    base = (FASTAPI_TEMPLATES / "base.html").read_text()
    assert "tailwind.config" in base, "base.html must configure the Tailwind CDN"
    assert re.search(r"darkMode\s*:\s*['\"]media['\"]", base), (
        "base.html must set darkMode: 'media' so prefers-color-scheme drives the theme"
    )


def test_django_css_defines_theme_tokens_for_both_schemes():
    """Every --sqlery-* token needs a light value and a dark override."""
    css = DJANGO_CSS.read_text()
    root_block = re.search(r":root\s*\{([^{}]*)\}", css)
    assert root_block, "dashboard.css must declare its tokens on a bare :root"
    light_tokens = set(re.findall(r"(--sqlery-[\w-]+)\s*:", root_block.group(1)))
    assert light_tokens, "dashboard.css declares no --sqlery-* theme tokens"

    dark_block = re.search(r'html\[data-theme="dark"\]\s*\{([^{}]*)\}', css)
    assert dark_block, "dashboard.css must override tokens for the explicit dark theme"
    dark_tokens = set(re.findall(r"(--sqlery-[\w-]+)\s*:", dark_block.group(1)))
    assert light_tokens == dark_tokens, (
        "every token needs both a light value and a dark override; "
        f"missing dark: {sorted(light_tokens - dark_tokens)}, "
        f"missing light: {sorted(dark_tokens - light_tokens)}"
    )
    assert "prefers-color-scheme: dark" in css, (
        "dashboard.css must also honour the admin's 'auto' theme setting"
    )
