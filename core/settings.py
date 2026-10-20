"""
Django settings for Minimarket Django Admin.
Comparte la misma BD PostgreSQL que la API FastAPI (minimarket-api).
Los modelos usan managed = False para no tocar el esquema de FastAPI.
"""

from pathlib import Path
import environ

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent

# ── Entorno ──────────────────────────────────────────────────────────────────
env = environ.Env(
    DEBUG=(bool, True),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
)
environ.Env.read_env(BASE_DIR / ".env")

# ── Seguridad ─────────────────────────────────────────────────────────────────
SECRET_KEY = env("SECRET_KEY", default="django-insecure-cambia-esto-en-produccion")
DEBUG       = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

# ── Aplicaciones ──────────────────────────────────────────────────────────────
INSTALLED_APPS = [
    # Admin personalizado (cargado primero para poder sobreescribir templates)
    "tienda.apps.TiendaConfig",
    # Django core
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Terceros
    "rest_framework",
]

# ── Middleware ────────────────────────────────────────────────────────────────
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
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

# ── Base de datos ─────────────────────────────────────────────────────────────
# Misma BD que FastAPI. Django NO migra ni crea tablas (managed=False en modelos).
# Solo la tabla django_migrations y las de auth/admin son gestionadas por Django.
DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgresql://postgres:postgres@localhost:5432/minimarket_db",
    )
}

# ── Modelo de usuario personalizado ───────────────────────────────────────────
# Usamos el modelo Usuario de la tienda que mapea la tabla "usuario" de FastAPI.
AUTH_USER_MODEL = "tienda.Usuario"

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
STATIC_URL  = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL   = "media/"
MEDIA_ROOT  = BASE_DIR / "media"

# ── DRF (Django REST Framework) ───────────────────────────────────────────────
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
}

# ── FastAPI integration ───────────────────────────────────────────────────────
FASTAPI_BASE_URL = env("FASTAPI_BASE_URL", default="http://localhost:8000")

# ── Miscelaneos ───────────────────────────────────────────────────────────────
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
