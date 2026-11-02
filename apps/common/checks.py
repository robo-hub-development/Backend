from django.conf import settings
from django.core.checks import Warning, register


@register(deploy=True)
def email_is_configured(app_configs, **kwargs):
    if settings.EMAIL_BACKEND.endswith("console.EmailBackend") and not settings.DEBUG:
        return [
            Warning(
                "Email is not configured: OTP codes are printed to the server log, not emailed.",
                hint="Set EMAIL_HOST (and EMAIL_HOST_USER / EMAIL_HOST_PASSWORD / DEFAULT_FROM_EMAIL).",
                id="robohub.W001",
            )
        ]
    return []
