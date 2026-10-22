from decouple import config, Csv
from .settings import *

# Secret and debug
SECRET_KEY = config("SECRET_KEY")
DEBUG = False

# Hosts and CSRF
ALLOWED_HOSTS = config("ALLOWED_HOSTS", cast=Csv())
CSRF_TRUSTED_ORIGINS = config("CSRF_TRUSTED_ORIGINS", default="", cast=Csv())

# Database: PostgreSQL
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": config("DB_NAME"),
        "USER": config("DB_USER"),
        "PASSWORD": config("DB_PASSWORD"),
        "HOST": config("DB_HOST", default="localhost"),
        "PORT": config("DB_PORT", default="5432"),
        "CONN_MAX_AGE": 60,
    }
}
r
# Password validators (Django defaults)
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Static files and WhiteNoise
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"
STORAGES = {
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"}
}

# Ensure WhiteNoise middleware is after SecurityMiddleware
try:
    _middleware = list(MIDDLEWARE)
    if "django.middleware.security.SecurityMiddleware" in _middleware:
        idx = _middleware.index("django.middleware.security.SecurityMiddleware")
        if "whitenoise.middleware.WhiteNoiseMiddleware" not in _middleware:
            _middleware.insert(idx + 1, "whitenoise.middleware.WhiteNoiseMiddleware")
    else:
        if "whitenoise.middleware.WhiteNoiseMiddleware" not in _middleware:
            _middleware.insert(0, "whitenoise.middleware.WhiteNoiseMiddleware")
    MIDDLEWARE = type(MIDDLEWARE)(_middleware)
except Exception:
    # best-effort: if MIDDLEWARE is not set as expected, create/override it
    MIDDLEWARE = [
        "django.middleware.security.SecurityMiddleware",
        "whitenoise.middleware.WhiteNoiseMiddleware",
    ] + list(globals().get("MIDDLEWARE", []))

# CORS
CORS_ALLOWED_ORIGINS = config("CORS_ALLOWED_ORIGINS", default="", cast=Csv())
CORS_ALLOWED_ORIGIN_REGEXES = []

# HTTPS / security settings
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = config("SECURE_SSL_REDIRECT", default=True, cast=bool)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = config("SECURE_HSTS_SECONDS", default=3600, cast=int)
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# Simple logging: errors to console
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "ERROR"},
}

# Admin contact
MAIN_ADMIN_EMAIL = config("MAIN_ADMIN_EMAIL", default=MAIN_ADMIN_EMAIL)
