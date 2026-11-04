from django.core.management.base import BaseCommand

from apps.payments import services


class Command(BaseCommand):
    help = (
        "Release stock held by online payments whose link has expired and which were never paid. "
        "Razorpay normally sends payment_link.expired; run this on a schedule as a safety net."
    )

    def handle(self, *args, **options):
        count = services.expire_stale_attempts()
        self.stdout.write(f"Released {count} expired payment attempt(s).")
