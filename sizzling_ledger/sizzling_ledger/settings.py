import json
import os
from pathlib import Path

import corsheaders

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv():
    """Read KEY=value lines from a `.env` file next to manage.py, so keys do not
    have to be typed into the terminal every time. Real environment variables
    always win over the file."""
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


_load_dotenv()
SECRET_KEY = "change-me-for-production"
DEBUG = True
ALLOWED_HOSTS = ["*"]

# Whoever logs in (via Google) with this exact email is permanently the
# main admin — full create/edit/delete access everywhere. No one can
# revoke this through the API. Swap this to the real Gmail address
# whenever you have it; it's the only place this needs to change.
MAIN_ADMIN_EMAIL = "shettymunna9741@gmail.com"

# --- GST lookup (registered name / address for a GST number) -----------------
# Format, state and check-digit validation always works with no setup. Fetching
# the registered name and address needs a GST data provider account, because
# the official GST portal is behind a captcha. Leave GST_LOOKUP_URL empty to
# run without one (nothing is invented; the app says details are unavailable).
#
#   GST_LOOKUP_URL      full URL; {gstin} and {api_key} are filled in, e.g.
#                       https://provider.example/taxpayer?gstin={gstin}
#   GST_LOOKUP_API_KEY  your provider key (substituted for {api_key})
#   GST_LOOKUP_HEADERS  optional JSON of request headers, e.g.
#                       {"x-api-key": "{api_key}"}
#   GST_LOOKUP_TIMEOUT  seconds to wait for the provider (default 10)
GST_LOOKUP_URL = os.environ.get('GST_LOOKUP_URL', '').strip()
GST_LOOKUP_API_KEY = os.environ.get('GST_LOOKUP_API_KEY', '').strip()
try:
    GST_LOOKUP_HEADERS = json.loads(os.environ.get('GST_LOOKUP_HEADERS', '') or '{}')
    if not isinstance(GST_LOOKUP_HEADERS, dict):
        GST_LOOKUP_HEADERS = {}
except ValueError:
    GST_LOOKUP_HEADERS = {}
try:
    GST_LOOKUP_TIMEOUT = int(os.environ.get('GST_LOOKUP_TIMEOUT', '10'))
except ValueError:
    GST_LOOKUP_TIMEOUT = 10
INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "rest_framework", "customers","rest_framework.authtoken","rest_framework_simplejwt", "corsheaders"
]
MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware", "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware", "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware", "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "sizzling_ledger.urls"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [], "APP_DIRS": True,
              "OPTIONS": {"context_processors": ["django.template.context_processors.request", "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages"]}}]
WSGI_APPLICATION = "sizzling_ledger.wsgi.application"
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}
AUTH_PASSWORD_VALIDATORS = []
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.TokenAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
}

CORS_ALLOWED_ORIGINS = [
    "http://localhost:8000",
    "http://localhost:5173",
    "http://localhost:5000",
    "http://127.0.0.1:8000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5000",
]
# The web build can run from any localhost port while you test it.
CORS_ALLOWED_ORIGIN_REGEXES = [r"^http://(localhost|127\.0\.0\.1)(:\d+)?$"]
# Where you host the web build, comma separated, e.g.
#   CORS_EXTRA_ORIGINS=https://app.example.com,http://192.168.0.141:5000
CORS_ALLOWED_ORIGINS += [
    o.strip() for o in os.environ.get("CORS_EXTRA_ORIGINS", "").split(",") if o.strip()
]
# The "Web application" Google client id. Empty until you put it in .env;
# Google sign-in is refused (with a clear message) while it is empty.
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
# Extra Google client ids whose sign-ins are accepted (the Windows app's
# "Desktop app" client), comma separated.
GOOGLE_EXTRA_CLIENT_IDS = [
    c.strip() for c in os.environ.get("GOOGLE_EXTRA_CLIENT_IDS", "").split(",") if c.strip()
]