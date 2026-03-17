"""Docker-specific settings for sqlery sample project."""

import os
import dj_database_url
from .settings import *

# Override settings for Docker environment
DEBUG = os.environ.get('DEBUG', '0') == '1'

ALLOWED_HOSTS = ['*']  # In production, set this to your domain

# Database configuration from environment variable
if os.environ.get('DATABASE_URL'):
    DATABASES = {
        'default': dj_database_url.config(
            default=os.environ['DATABASE_URL'],
            conn_max_age=600,
        )
    }
else:
    # Fallback to PostgreSQL with default credentials
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.environ.get('POSTGRES_DB', 'sqlery'),
            'USER': os.environ.get('POSTGRES_USER', 'postgres'),
            # 'PASSWORD': os.environ.get('POSTGRES_PASSWORD', 'postgres'),
            'PASSWORD': os.environ.get('POSTGRES_PASSWORD', ''),
            'HOST': os.environ.get('POSTGRES_HOST', 'db'),
            'PORT': os.environ.get('POSTGRES_PORT', '5432'),
        }
    }

# Static files
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATIC_URL = '/static/'
