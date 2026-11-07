from .base import *  # noqa: F403
from .base import env

DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]
API_DOCS_ENABLED = env.bool("API_DOCS_ENABLED", default=True)

# Every OTP is 123456 locally (matches the storefront mocks); set OTP_FIXED_CODE= (empty) for random codes.
OTP_FIXED_CODE = env("OTP_FIXED_CODE", default="123456")
