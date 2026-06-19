from pathlib import Path
import os
from urllib.parse import urlparse

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

def env_bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


DEBUG = env_bool("DJANGO_DEBUG", "1")
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "dev-secret-key"
    else:
        raise ValueError("DJANGO_SECRET_KEY nao definido. Configure uma chave segura em producao.")

try:
    import whitenoise  # noqa: F401

    WHITENOISE_AVAILABLE = True
except ImportError:
    WHITENOISE_AVAILABLE = False

ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,0.0.0.0").split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "invoices",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

if WHITENOISE_AVAILABLE:
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
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

WSGI_APPLICATION = "config.wsgi.application"


def database_config() -> dict:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        if DEBUG:
            return {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": BASE_DIR / "db.sqlite3",
            }
        raise ValueError("DATABASE_URL não definido. Configure o PostgreSQL em ambiente nao-desenvolvimento.")

    parsed = urlparse(database_url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("DATABASE_URL deve usar postgres:// ou postgresql://")

    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": parsed.path.lstrip("/"),
        "USER": parsed.username,
        "PASSWORD": parsed.password,
        "HOST": parsed.hostname,
        "PORT": parsed.port or 5432,
    }


DATABASES = {"default": database_config()}

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
if WHITENOISE_AVAILABLE:
    STORAGES = {
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "loggers": {
        "invoices": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}

MAX_UPLOAD_SIZE = 10 * 1024 * 1024
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
_LEGACY_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "")
GEMINI_EXTRACTION_MODEL = os.getenv("GEMINI_EXTRACTION_MODEL", _LEGACY_GEMINI_MODEL or "gemini-2.5-flash")
GEMINI_EXTRACTION_MAX_OUTPUT_TOKENS = int(os.getenv("GEMINI_EXTRACTION_MAX_OUTPUT_TOKENS", "8192"))
GEMINI_RAG_MODEL = os.getenv("GEMINI_RAG_MODEL", _LEGACY_GEMINI_MODEL or "gemini-2.5-flash-lite")
GEMINI_RAG_MAX_OUTPUT_TOKENS = int(os.getenv("GEMINI_RAG_MAX_OUTPUT_TOKENS", "900"))
GEMINI_MODEL = GEMINI_EXTRACTION_MODEL
