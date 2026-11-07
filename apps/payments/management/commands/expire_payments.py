from django.core.management.base import BaseCommand

from apps.payments import services


class Command(BaseCommand):
    help = (
        "Expire orders that were never paid within their payment window and release their stock. "
        "Razorpay normally sends payment_link.expired; run this on a schedule as a safety net. "
        "It also settles refunds still in progress when their webhook (refund.processed) never arrived."
    )

    def handle(self, *args, **options):
        count = services.expire_unpaid_orders()
        self.stdout.write(f"Expired {count} unpaid order(s) and released their stock.")
        refunds = services.reconcile_refunds()
        self.stdout.write(f"Settled {refunds} refund(s) that were in progress.")
