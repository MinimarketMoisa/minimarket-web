"""
Django settings — Minimarket Admin Portal.
Comparte BD PostgreSQL con FastAPI; los modelos usan managed=False.
"""

from importlib.util import find_spec
from pathlib import Path
import environ

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent

# ── Entorno ──────────────────────────────────────────────────────────────────
env = environ.Env(
    DEBUG=(bool, True),
    ALLOWED_HOSTS=(list, [
        "localhost",
        "127.0.0.1",
        "64.236.138.169",
        "minimarket-api.duckdns.org",
        "*",
    ]),
    CSRF_TRUSTED_ORIGINS=(list, [
        "https://minimarket-api.duckdns.org",
        "http://minimarket-api.duckdns.org",
        "http://minimarket-api.duckdns.org:8001",
        "http://minimarket-api.duckdns.org:8080",
        "http://64.236.138.169",
        "http://64.236.138.169:8001",
        "http://64.236.138.169:8080",
        "http://127.0.0.1:8080",
        "http://localhost:8080",
        "http://127.0.0.1:8001",
        "http://localhost:8001",
    ]),
)
environ.Env.read_env(BASE_DIR / ".env")

# ── Seguridad ─────────────────────────────────────────────────────────────────
SECRET_KEY = env("SECRET_KEY", default="django-insecure-cambia-esto-en-produccion")
JWT_SECRET = env("JWT_SECRET", default="")
DEBUG       = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env("CSRF_TRUSTED_ORIGINS")
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"


# ── Aplicaciones ──────────────────────────────────────────────────────────────
INSTALLED_APPS = [
    # Admin personalizado (cargado primero para poder sobreescribir templates)
    "tienda.apps.TiendaConfig",
    # Django core
    "tienda.apps.MinimarketAdminConfig",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

# ── Middleware ────────────────────────────────────────────────────────────────
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    *(
        ["whitenoise.middleware.WhiteNoiseMiddleware"]
        if find_spec("whitenoise") is not None
        else []
    ),
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "core.urls"

# ── Templates ─────────────────────────────────────────────────────────────────
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

WSGI_APPLICATION = "core.wsgi.application"
ASGI_APPLICATION = "core.asgi.application"

# BD compartida con FastAPI — Django no migra ni crea tablas (managed=False).
# Acepta DATABASE_URL completa o variables individuales.
if env("DATABASE_URL", default=None):
    DATABASES = {"default": env.db("DATABASE_URL")}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("DB_NAME", default="minimarket_db"),
            "USER": env("DB_USER", default="postgres"),
            "PASSWORD": env("DB_PASSWORD", default="postgres"),
            "HOST": env("DB_HOST", default="localhost"),
            "PORT": env("DB_PORT", default="5432"),
        }
    }

# Reutilizar conexiones TCP hacia PostgreSQL (especialmente útil contra VPS remota)
DATABASES["default"]["CONN_MAX_AGE"] = env.int("CONN_MAX_AGE", default=60)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True


# ── Modelo de usuario y backend optimizado ───────────────────────────────────
# Usa el modelo Usuario de tienda que mapea la tabla "usuario" de FastAPI.
AUTH_USER_MODEL = "tienda.Usuario"
AUTHENTICATION_BACKENDS = ["tienda.services.MinimarketAuthBackend"]

# ── Autenticacion de contrasenas ──────────────────────────────────────────────
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ── Internacionalizacion ──────────────────────────────────────────────────────
LANGUAGE_CODE = "es-gt"
TIME_ZONE     = "America/Guatemala"
USE_I18N      = True
USE_TZ        = True

# ── Archivos estaticos ────────────────────────────────────────────────────────
STATIC_URL  = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
WHITENOISE_USE_FINDERS = True
MEDIA_URL   = "/media/"
MEDIA_ROOT  = BASE_DIR / "media"

# ── FastAPI integration ───────────────────────────────────────────────────────
FASTAPI_BASE_URL = env("FASTAPI_BASE_URL", default="https://minimarket-api.duckdns.org")
FASTAPI_WS_URL   = env("FASTAPI_WS_URL", default="wss://minimarket-api.duckdns.org")


# ── Miscelaneos ───────────────────────────────────────────────────────────────
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
