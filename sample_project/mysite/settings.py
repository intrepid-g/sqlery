"""Django settings for sample project using sqlery."""

import os
import sys
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Add the sqlery package to the path (for local development)
sys.path.insert(0, str(BASE_DIR.parent / 'src'))

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = 'django-insecure-sample-project-key-do-not-use-in-production'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

# SECURITY: Safe defaults - only localhost access
# Set DJANGO_ALLOW_REMOTE=1 environment variable to allow network access
ALLOWED_HOSTS = ['localhost', '127.0.0.1']
if os.environ.get('DJANGO_ALLOW_REMOTE') == '1':
    ALLOWED_HOSTS = ['*']

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Sqlery
    # 'sqlery',  # Old location
    'sqlery.django_sqlery',

    # Sample tasks app
    'tasks_app',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',

    # Sqlery daemon middleware (continuous background worker)
    # 'sqlery.daemon_middleware.DaemonMiddleware',  # Old location
    'sqlery.django_sqlery.daemon_middleware.DaemonMiddleware',
]

ROOT_URLCONF = 'mysite.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'mysite.wsgi.application'

# Database
# Support both SQLite and PostgreSQL based on environment variables
if os.environ.get('USE_POSTGRES') == '1':
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.environ.get('POSTGRES_DB', 'sqlery_test'),
            'USER': os.environ.get('POSTGRES_USER', 'sqlery'),
            # 'PASSWORD': os.environ.get('POSTGRES_PASSWORD', 'sqlery_password'),
            'PASSWORD': os.environ.get('POSTGRES_PASSWORD', ''),
            'HOST': os.environ.get('POSTGRES_HOST', 'localhost'),
            'PORT': os.environ.get('POSTGRES_PORT', '5432'),
        }
    }
else:
    # Use /app/data for Docker volume persistence, or local path otherwise
    DB_PATH = os.environ.get('SQLITE_PATH', str(BASE_DIR / 'db.sqlite3'))
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': DB_PATH,
        }
    }

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Sqlery Configuration
DJANGO_SQL_JOBS = {
    # Trigger mode: 'daemon' runs a continuous background worker
    'TRIGGER_MODE': 'daemon',
    'ENABLE_DAEMON': True,
    'DAEMON_CHECK_INTERVAL': 10,  # Check for jobs every 10 seconds

    # Multi-worker configuration
    'MAX_WORKERS_PER_NODE': 3,  # Enable 3 workers for testing
    'WORKER_QUEUES': ['high', 'default', 'low'],
    'QUEUE_PRIORITIES': {
        'high': 100,
        'default': 50,
        'low': 10,
    },

    # Queue defaults
    'DEFAULT_QUEUE': 'default',
    'DEFAULT_PRIORITY': 0,

    # Registries
    'ENABLE_REGISTRIES': True,

    # Retention
    'JOB_RETENTION': {
        'success_max_age_days': 1,  # Short for testing
        'failed_max_age_days': 7,
    },

    # Retry defaults
    'DEFAULT_MAX_RETRIES': 3,
    'DEFAULT_RETRY_BACKOFF': 1.0,
}

# Logging configuration
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'sqlery': {
            'handlers': ['console'],
            'level': 'INFO',
        },
    },
}
