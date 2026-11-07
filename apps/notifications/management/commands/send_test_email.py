from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        "Send one test email through the configured mail backend, to check the SMTP settings "
        "(EMAIL_HOST, EMAIL_PORT, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD, EMAIL_USE_TLS / EMAIL_USE_SSL)."
    )

    def add_arguments(self, parser):
        parser.add_argument("to", help="Address that should receive the test email.")

    def handle(self, *args, to, **options):
        backend = settings.EMAIL_BACKEND.rsplit(".", 2)[-2]
        where = f"{settings.EMAIL_HOST}:{settings.EMAIL_PORT}" if settings.EMAIL_HOST else "no EMAIL_HOST set"
        self.stdout.write(f"Backend: {backend} ({where}); from: {settings.DEFAULT_FROM_EMAIL}")
        try:
            sent = send_mail(
                subject=f"{settings.SELLER['name']}: email test",
                message="If you can read this, outgoing email works.",
                from_email=None,
                recipient_list=[to],
                fail_silently=False,
            )
        except Exception as exc:  # shown to the operator; the password is never part of the message
            raise CommandError(f"Sending failed: {type(exc).__name__}: {exc}") from exc
        if not sent:
            raise CommandError("The mail backend accepted nothing to send.")
        note = "" if settings.EMAIL_HOST else " (console backend: it was printed above, not delivered)"
        self.stdout.write(self.style.SUCCESS(f"Test email sent to {to}.{note}"))
