"""
Django settings for the Procurement module.

Target runtime: Vercel (serverless Python) + Neon (Postgres) + Vercel Blob (files).
Everything environment-specific is read from environment variables.
"""
import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default='.env'):
    return os.environ.get(name, default)


# --------------------------------------------------------------------------- core
DEBUG = env("DEBUG", "0") == "1"
SECRET_KEY = env("SECRET_KEY") or ("dev-only-insecure-key" if DEBUG else None)
if not SECRET_KEY:
    raise ImproperlyConfigured("SECRET_KEY must be set when DEBUG is off.")

ALLOWED_HOSTS = [h.strip() for h in env("ALLOWED_HOSTS", ".vercel.app,localhost,127.0.0.1").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = ["https://*.vercel.app"] + [
    o.strip() for o in env("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # project apps
    "apps.core",
    "apps.audit",
    "apps.accounts",
    "apps.masterdata",
    "apps.doa",
    "apps.requisitions",
    "apps.sourcing",
    "apps.documents",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.audit.middleware.CurrentRequestMiddleware",  # lets audit.record() capture IP
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

# --------------------------------------------------------------------------- database (Neon)
_db_url = env("DATABASE_URL")
try: 
    if env("USE_DIRECT_DB") == "1":
        # Migrations should use Neon's direct (non-pooled) connection.
        _db_url = env("DATABASE_URL_UNPOOLED") or _db_url

    if _db_url:
        DATABASES = {"default": dj_database_url.parse(_db_url, conn_max_age=0, ssl_require=not DEBUG)}
        # Required with Neon's pooled (pgbouncer, transaction mode) endpoint.
        DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True
except Exception as e:
    print(e)
finally:  # local development fallback
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------- auth
AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = [
    "apps.accounts.backends.HardcodedAdminBackend",  # built-in Admin account
    "django.contrib.auth.backends.ModelBackend",  # every other user (created by Admin)
]
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "accounts:login"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

# The Admin account is HARD-CODED by requirement. It is not stored with a usable
# password in the DB and cannot be edited from the UI. Change these values before go-live
# (this file is the single place they live).
HARDCODED_ADMIN = {
    "username": "admin",
    "password": "Admin@12345",
    "email": "admin@example.com",
    "first_name": "System",
    "last_name": "Admin",
}

# --------------------------------------------------------------------------- i18n / static
LANGUAGE_CODE = "en-us"
TIME_ZONE = env("TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# Serve static files straight from app directories so no `collectstatic` is needed on Vercel.
WHITENOISE_USE_FINDERS = True

# --------------------------------------------------------------------------- security
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")  # Vercel terminates TLS
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
SESSION_COOKIE_AGE = 60 * 60 * 8  # 8h working-day session

# --------------------------------------------------------------------------- file storage (Vercel Blob)
BLOB_READ_WRITE_TOKEN = env("BLOB_READ_WRITE_TOKEN", "")
# "vercel" when a token is present, otherwise "local" (writes under BLOB_LOCAL_ROOT; dev/test only).
BLOB_BACKEND = env("BLOB_BACKEND", "vercel" if BLOB_READ_WRITE_TOKEN else "local")
BLOB_API_URL = env("BLOB_API_URL", "https://blob.vercel-storage.com")
BLOB_API_VERSION = env("BLOB_API_VERSION", "7")  # verify with `manage.py blob_smoketest`
BLOB_LOCAL_ROOT = env("BLOB_LOCAL_ROOT", str(BASE_DIR / ".local_blob"))

# Uploads go through Django, and Vercel caps request bodies at ~4.5 MB.
MAX_UPLOAD_BYTES = int(env("MAX_UPLOAD_BYTES", str(4 * 1024 * 1024)))
ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".png", ".jpg", ".jpeg"}
DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_BYTES + 512 * 1024

# --------------------------------------------------------------------------- business switches
ALLOW_MULTIPLE_RFQ_PER_PR = env("ALLOW_MULTIPLE_RFQ_PER_PR", "0") == "1"
CRON_SECRET = env("CRON_SECRET", "")
