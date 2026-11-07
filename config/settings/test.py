from .base import *  # noqa: F403

DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost"]
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
NOTIFICATIONS_ENABLED = True  # tests cover the emails; a developer's .env must not switch them off
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# Generous limits so unrelated tests never trip throttles; throttle tests patch rates explicitly.
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_THROTTLE_RATES": {k: "10000/min" for k in REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]},
}
OTP_RESEND_COOLDOWN_SECONDS = 0
API_DOCS_ENABLED = True

PAYMENT_GATEWAY = "fake"
RAZORPAY_WEBHOOK_SECRET = "whsec_test"  # noqa: S105 - test-only value
RAZORPAY_KEY_ID = "rzp_test_key"
RAZORPAY_KEY_SECRET = "test_key_secret"  # noqa: S105 - test-only value
