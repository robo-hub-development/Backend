from django.core.management.base import BaseCommand

from apps.notifications import services


class Command(BaseCommand):
    help = (
        "Send again the customer emails that failed (up to 3 attempts each). Run it on a schedule, "
        "e.g. every 15 minutes, next to expire_payments."
    )

    def handle(self, *args, **options):
        sent = services.retry_failed()
        self.stdout.write(f"Re-sent {sent} email(s) that had failed.")
