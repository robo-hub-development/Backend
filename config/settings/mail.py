"""Email delivery settings, read from the environment (Django's own SMTP backend; no extra package).

Set EMAIL_HOST and the account is used over SMTP. Leave it empty and mail is printed to the console, so a
first run or a local checkout works with no mail server (production warns via the robohub.W001 check).
"""

from django.core.exceptions import ImproperlyConfigured

SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
CONSOLE_BACKEND = "django.core.mail.backends.console.EmailBackend"


def email_settings(env, sender_name: str) -> dict:
    host = env("EMAIL_HOST", default="").strip()
    user = env("EMAIL_HOST_USER", default="").strip()
    use_ssl = env.bool("EMAIL_USE_SSL", default=False)
    use_tls = env.bool("EMAIL_USE_TLS", default=not use_ssl)  # STARTTLS on 587 is the common default
    if use_ssl and use_tls:
        raise ImproperlyConfigured("EMAIL_USE_TLS and EMAIL_USE_SSL cannot both be true: pick one.")
    # Providers authenticate the account and often rewrite the sender to it, so default to that address.
    default_from = f"{sender_name} <{user}>" if "@" in user else "no-reply@robohub.local"
    return {
        "EMAIL_BACKEND": SMTP_BACKEND if host else CONSOLE_BACKEND,
        "EMAIL_HOST": host,
        "EMAIL_PORT": env.int("EMAIL_PORT", default=465 if use_ssl else 587),
        "EMAIL_HOST_USER": user,
        "EMAIL_HOST_PASSWORD": env("EMAIL_HOST_PASSWORD", default=""),
        "EMAIL_USE_TLS": use_tls,
        "EMAIL_USE_SSL": use_ssl,
        "EMAIL_TIMEOUT": env.int("EMAIL_TIMEOUT", default=10),  # a slow server must not stall a request
        "DEFAULT_FROM_EMAIL": env("DEFAULT_FROM_EMAIL", default=default_from),
        "SERVER_EMAIL": env("SERVER_EMAIL", default=default_from),
    }
