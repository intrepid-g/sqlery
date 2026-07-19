"""Django settings for run_modes_lab."""
import os
from urllib.parse import urlparse

# Secret key — required, fail fast if unset
SECRET_KEY = os.environ["SECRET_KEY"]

# Debug mode — default False
DEBUG = os.environ.get("DEBUG", "false").lower() == "true"

# Lab only: allow all hosts
# Note: This is NOT secure for production. Real deployments must restrict ALLOWED_HOSTS.
ALLOWED_HOSTS = ["*"]

# Database configuration from DATABASE_URL
# Parse URL manually (dj-database-url not available as dependency per CLAUDE.md)
_db_url = os.environ.get("DATABASE_URL", "sqlite:///db.sqlite3")
_parsed = urlparse(_db_url)

if _parsed.scheme == "postgresql":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": _parsed.path.lstrip("/"),
            "USER": _parsed.username or "postgres",
            "PASSWORD": _parsed.password or "",
            "HOST": _parsed.hostname or "localhost",
            "PORT": _parsed.port or 5432,
        }
    }
elif _parsed.scheme == "sqlite":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": _parsed.path or "db.sqlite3",
        }
    }
else:
    raise ValueError(f"Unsupported DATABASE_URL scheme: {_parsed.scheme}")

# Django apps
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "sqlery.django_sqlery",  # Sqlery Django app
    "django_tasks",  # django-tasks: required for EXECUTION_MODE=django-tasks (async-worker)
    "django_tasks.backends.database",  # provides the `manage.py db_worker` command
    "lab_jobs",  # Lab tasks app
]

# django-tasks backend configuration — required for the async-worker mode
# (EXECUTION_MODE=django-tasks / USE_DJANGO_TASKS=true) to have a working
# `manage.py db_worker` command. See django_tasks.backends.database.backend.DatabaseBackend.
TASKS = {
    "default": {
        "BACKEND": "django_tasks.backends.database.DatabaseBackend",
    }
}

# Middleware — includes session/auth/messages for admin access
# The middleware trigger mode (TRIGGER_MODE=middleware) relies on Django request/response
# middleware to fire trigger_queue_workers() on each request (synchronous thread mode).
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# Templates
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Internationalization
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = "/static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Caching (in-memory, single-process)
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

# Logging
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "sqlery": {
            "level": "DEBUG",
        },
    },
}

# Sqlery configuration
# See src/sqlery/django_sqlery/settings.py DEFAULTS for all available keys.
DJANGO_SQL_JOBS = {
    # Trigger mode: how execution gets kicked off (middleware, http, subprocess, daemon, eventbridge, disabled)
    "TRIGGER_MODE": os.environ.get("TRIGGER_MODE", "middleware"),
    # Execution mode: how jobs are actually executed (auto, subprocess, thread, django-tasks)
    "EXECUTION_MODE": os.environ.get("EXECUTION_MODE", "auto"),
    # Daemon mode: enable background daemon worker
    "ENABLE_DAEMON": os.environ.get("ENABLE_DAEMON", "false").lower() == "true",
    # Django-tasks async backend
    "USE_DJANGO_TASKS": os.environ.get("USE_DJANGO_TASKS", "false").lower() == "true",
    # HTTP trigger security
    "INTERNAL_SECRET": os.environ.get("INTERNAL_SECRET"),
    "INTERNAL_BASE_URL": os.environ.get("INTERNAL_BASE_URL"),
    "INTERNAL_ALLOWED_IPS": (
        os.environ.get("INTERNAL_ALLOWED_IPS", "127.0.0.1,::1").split(",")
    ),
    "SIGNATURE_MAX_AGE": int(os.environ.get("SIGNATURE_MAX_AGE", "10")),
}
