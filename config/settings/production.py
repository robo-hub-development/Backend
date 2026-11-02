from .base import *  # noqa: F403
from .base import env

DEBUG = False
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS")  # required in production

# No localhost fallback in production: a missing DATABASE_URL must fail loudly with its name.
DATABASES["default"] = {  # noqa: F405
    **env.db("DATABASE_URL"),
    "ATOMIC_REQUESTS": False,  # transactions are explicit in services
    "CONN_MAX_AGE": env.int("DB_CONN_MAX_AGE", default=60),
}

SECURE_PROXY_SSL_HEADER =("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = env("EMAIL_HOST")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL")

# Hard-coded off: a fixed OTP would let anyone log in as any customer.
OTP_FIXED_CODE = ""

# Static files (admin CSS/JS, Swagger assets) are served by WhiteNoise from STATIC_ROOT after `collectstatic`.
MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")  # noqa: F405 - right after SecurityMiddleware
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# Admin login over https behind Render's proxy, e.g. https://robo-hub-api.onrender.com
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
