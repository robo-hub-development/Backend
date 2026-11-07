from django.conf import settings
from django.core.checks import Warning, register


@register(deploy=True)
def email_is_configured(app_configs, **kwargs):
    if settings.EMAIL_BACKEND.endswith("console.EmailBackend") and not settings.DEBUG:
        return [
            Warning(
                "SMTP is not configured: OTP codes and order emails are printed to the server log, not sent.",
                hint="Set EMAIL_HOST (and EMAIL_HOST_USER / EMAIL_HOST_PASSWORD), then run send_test_email.",
                id="robohub.W001",
            )
        ]
    return []


@register(deploy=True)
def fixed_otp_is_off(app_configs, **kwargs):
    if settings.OTP_FIXED_CODE and not settings.DEBUG:
        return [
            Warning(
                "A fixed OTP is active: anyone who knows an email can log in as that user.",
                hint="Remove the INSECURE_FIXED_OTP environment variable before real users arrive.",
                id="robohub.W002",
            )
        ]
    return []


@register(deploy=True)
def payments_are_configured(app_configs, **kwargs):
    if settings.PAYMENT_GATEWAY == "razorpay" and not (
        settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET and settings.RAZORPAY_WEBHOOK_SECRET
    ):
        return [
            Warning(
                "Razorpay is not configured: online checkout answers 503 until it is.",
                hint="Set RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET and RAZORPAY_WEBHOOK_SECRET.",
                id="robohub.W003",
            )
        ]
    return []
