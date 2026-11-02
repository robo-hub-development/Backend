from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.models import AuthToken, OtpChallenge


class Command(BaseCommand):
    help = "Delete expired OTP challenges and expired/revoked auth tokens (run daily from cron)."

    def handle(self, *args, **options):
        now = timezone.now()
        challenges, _ = OtpChallenge.objects.filter(expires_at__lt=now - timedelta(days=1)).delete()
        tokens, _ = AuthToken.objects.filter(expires_at__lt=now).delete()
        revoked, _ = AuthToken.objects.filter(revoked_at__isnull=False).delete()
        self.stdout.write(f"Purged {challenges} challenges, {tokens} expired and {revoked} revoked tokens.")
