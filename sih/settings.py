from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# WARNING: replace SECRET_KEY and set ALLOWED_HOSTS before any non-local deployment.
SECRET_KEY = "sih-demo-secret-key"
DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

INSTALLED_APPS = [
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "web",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "sih.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "web" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "sih.wsgi.application"

# Cookie-based sessions/messages: the frontend uses django.contrib.messages
# purely to show "document uploaded" / "processing failed" banners, so it
# is kept fully stateless and does not require any database migrations.
SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
MESSAGE_STORAGE = "django.contrib.messages.storage.cookie.CookieStorage"


LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "web" / "static"]

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "data"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
