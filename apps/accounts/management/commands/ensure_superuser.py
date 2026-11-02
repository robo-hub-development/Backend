import os

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError

from apps.accounts.models import User
from apps.accounts.validators import validate_mobile


class Command(BaseCommand):
    help = (
        "Create the first admin from DJANGO_SUPERUSER_EMAIL / _NAME / _MOBILE / _PASSWORD. "
        "For hosts without shell access (e.g. Render free). No-op if the email is unset or already exists."
    )

    def handle(self, *args, **options):
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "").strip().lower()
        if not email:
            self.stdout.write("DJANGO_SUPERUSER_EMAIL is not set; skipping admin creation.")
            return
        if User.objects.filter(email=email).exists():
            self.stdout.write(f"Admin {email} already exists; nothing to do.")
            return

        values = {
            field: os.environ.get(f"DJANGO_SUPERUSER_{field.upper()}", "").strip()
            for field in ("name", "mobile", "password")
        }
        missing = [f"DJANGO_SUPERUSER_{f.upper()}" for f, v in values.items() if not v]
        if missing:
            raise CommandError("Missing environment variables: " + ", ".join(missing))

        try:
            validate_mobile(values["mobile"])
            validate_password(values["password"])
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages)) from None

        try:
            User.objects.create_superuser(email=email, **values)
        except IntegrityError:
            raise CommandError("That mobile number already belongs to another account.") from None
        self.stdout.write(self.style.SUCCESS(f"Created admin {email}."))
