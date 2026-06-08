"""Django settings for tests."""
import os

SECRET_KEY = "test-secret-key"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# WR-02 (11-REVIEW): the Django x Postgres parity cells previously ran against
# in-memory SQLite (hardcoded below) even on the PG rail, so their "on Postgres'
# MVCC / row-lock semantics" docstrings were not actually exercised. Make the
# Django test DB env-driven: use Postgres when SQLERY_TEST_PG_URL (preferred,
# matches the PG rail's env) or DATABASE_URL points at a postgres URL, falling
# back to the original in-memory SQLite for the default (SQLite-only) rails.
# Old (hardcoded in-memory SQLite — never touched Postgres):
# DATABASES = {
#     "default": {
#         "ENGINE": "django.db.backends.sqlite3",
#         "NAME": ":memory:",
#     }
# }
_PG_URL = os.environ.get("SQLERY_TEST_PG_URL") or os.environ.get("DATABASE_URL")


def _is_postgres_url(url: str | None) -> bool:
    """True for postgres:// or postgresql:// (incl. +driver) DSNs."""
    if not url:
        return False
    scheme = url.split("://", 1)[0].lower()
    return scheme == "postgres" or scheme == "postgresql" or scheme.startswith("postgresql+")


if _is_postgres_url(_PG_URL):
    # Parse the DSN into Django's NAME/USER/PASSWORD/HOST/PORT fields.
    from urllib.parse import urlparse, unquote

    _parsed = urlparse(_PG_URL)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(_parsed.path.lstrip("/")) or "postgres",
            "USER": unquote(_parsed.username) if _parsed.username else "",
            "PASSWORD": unquote(_parsed.password) if _parsed.password else "",
            "HOST": _parsed.hostname or "",
            "PORT": str(_parsed.port) if _parsed.port else "",
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": ":memory:",
        }
    }

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.admin",
    "sqlery.django_sqlery",
]

MIDDLEWARE = [
    "django.middleware.common.CommonMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

USE_TZ = True

ROOT_URLCONF = "sqlery.django_sqlery.urls"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

DJANGO_SQL_JOBS = {
    "ENABLE_MIDDLEWARE_TRIGGER": False,  # Don't trigger in tests
    "USE_DJANGO_TASKS": False,  # Use sync execution in tests
}
