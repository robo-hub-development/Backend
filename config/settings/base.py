"""Shared settings. Every environment-specific value comes from env vars."""

from pathlib import Path

import environ

from config.api.versions import API_VERSIONS, DEFAULT_API_VERSION

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
# Optional .env file for local development; real environments inject env vars.
environ.Env.read_env(BASE_DIR / ".env", overwrite=False)

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = False
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])

# Public origin of the Next.js storefront (used for payment callbacks, CORS).
SITE_URL = env("SITE_URL", default="http://localhost:3000")

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]
THIRD_PARTY_APPS = [
    "rest_framework",
    "corsheaders",
    "drf_spectacular",
]
# Feature apps are appended here as each phase lands.
LOCAL_APPS = [
    "apps.common",
    "apps.accounts",
    "apps.catalog",
    "apps.cart",
    "apps.orders",
]
INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
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

DATABASES = {
    "default": env.db("DATABASE_URL", default="postgres://postgres:postgres@localhost:5432/robohub"),
}
DATABASES["default"]["ATOMIC_REQUESTS"] = False  # transactions are explicit in services
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

# OTP / session settings
# Development shortcut: when set, every OTP is this value. Must stay empty outside local settings.
OTP_FIXED_CODE = ""
OTP_TTL_SECONDS = env.int("OTP_TTL_SECONDS", default=120)
OTP_MAX_ATTEMPTS = env.int("OTP_MAX_ATTEMPTS", default=5)
OTP_RESEND_COOLDOWN_SECONDS = env.int("OTP_RESEND_COOLDOWN_SECONDS", default=30)
AUTH_TOKEN_TTL_SECONDS = env.int("AUTH_TOKEN_TTL_SECONDS", default=604800)  # 7 days
# Session tokens are HS256 JWTs. Rotating the key signs every user out.
JWT_SIGNING_KEY = env("JWT_SIGNING_KEY", default=SECRET_KEY)
JWT_ALGORITHM = "HS256"
JWT_ISSUER = env("JWT_ISSUER", default="robohub-api")

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# Customers may cancel a pending/confirmed order for this long after placing it (PRD 1.6: limited time).
ORDER_CANCEL_WINDOW_HOURS = env.int("ORDER_CANCEL_WINDOW_HOURS", default=24)

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"
# Product media upload limits (also set the reverse-proxy body size to at least the video limit).
CATALOG_MAX_IMAGE_BYTES = env.int("CATALOG_MAX_IMAGE_BYTES", default=5 * 1024 * 1024)
CATALOG_MAX_VIDEO_BYTES = env.int("CATALOG_MAX_VIDEO_BYTES", default=100 * 1024 * 1024)

# CORS: the storefront calls public GET endpoints from the browser; everything
# authenticated goes through the Next.js BFF (server-to-server).
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[SITE_URL])
CORS_ALLOW_CREDENTIALS = False

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["apps.common.renderers.EnvelopeJSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_PAGINATION_CLASS": "apps.common.pagination.StandardPagination",
    "EXCEPTION_HANDLER": "apps.common.exceptions.api_exception_handler",
    "DEFAULT_AUTHENTICATION_CLASSES": ["apps.accounts.authentication.BearerTokenAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_THROTTLE_CLASSES": [],
    "DEFAULT_THROTTLE_RATES": {
        "otp_send": env("THROTTLE_OTP_SEND", default="30/hour"),
        "otp_verify": env("THROTTLE_OTP_VERIFY", default="60/hour"),
        "newsletter": env("THROTTLE_NEWSLETTER", default="10/hour"),
        "order_place": env("THROTTLE_ORDER_PLACE", default="30/hour"),
    },
    "DEFAULT_VERSIONING_CLASS": "rest_framework.versioning.NamespaceVersioning",
    "DEFAULT_VERSION": DEFAULT_API_VERSION,
    "ALLOWED_VERSIONS": tuple(API_VERSIONS),
    # Behind a proxy/BFF, set NUM_PROXIES so per-IP throttles see the real client address.
    "NUM_PROXIES": env.int("NUM_PROXIES", default=None),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "UNAUTHENTICATED_USER": None,
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
    "loggers": {
        # Never log request bodies/headers here: they carry OTPs and tokens.
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}

# API documentation (OpenAPI via drf-spectacular). Off unless enabled; never expose publicly by accident.
API_DOCS_ENABLED = env.bool("API_DOCS_ENABLED", default=False)
SPECTACULAR_SETTINGS = {
    "TITLE": "Stem Robo Hub API",
    "DESCRIPTION": (
        "Backend API for the Stem Robo Hub storefront.\n\n"
        "**Auth:** call `POST /auth/otp/send` then `POST /auth/otp/verify`, then send "
        "`Authorization: Bearer <token>` on routes marked with a lock.\n\n"
        '**Errors:** non-2xx responses return `{"message": str, "errors": {field: [str]}}`.'
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SORT_OPERATIONS": False,
    "POSTPROCESSING_HOOKS": [
        "drf_spectacular.hooks.postprocess_schema_enums",
        "apps.common.api_docs.wrap_envelope",  # documents the {success, data, error} wrapper
    ],
    "SWAGGER_UI_SETTINGS": {"persistAuthorization": True, "displayRequestDuration": True},
}
