"""Settings for svccat-catalog: a standalone Service Broker catalog (no
Kubernetes). Django + DRF; SQLite for dev, PostgreSQL for production via
DATABASE_URL. Credentials are encrypted at rest with Fernet."""

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SECRET_KEY = os.environ.get("SESSION_SECRET", "dev-insecure-session-secret")
DEBUG = os.environ.get("DJANGO_DEBUG") == "1"
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "rest_framework",
    "django_filters",
    "rest_framework.authtoken",
    "catalog",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "catalog.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
            ]
        },
    }
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.path.join(BASE_DIR, "db.sqlite3"),
    }
}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
    ],
    # pagination off: the platform's datasets are small (tens, not
    # thousands); if it grows, turn on PageNumberPagination
}

# Credential encryption: Fernet key derived from CREDENTIAL_KEY (env) so
# it survives restarts; the fallback is deterministic for dev
import base64
import hashlib

_raw = os.environ.get("CREDENTIAL_KEY", "dev-insecure-credential-key").encode()
CREDENTIAL_KEY = base64.urlsafe_b64encode(hashlib.sha256(_raw).digest())

USE_TZ = True
TIME_ZONE = "UTC"
