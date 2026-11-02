from datetime import timedelta

import pytest
from django.core.management import call_command
from django.db import IntegrityError
from django.utils import timezone

from apps.accounts.models import AuthToken, OtpChallenge, User

pytestmark = pytest.mark.django_db


def test_email_normalised_and_unique():
    User.objects.create_user(email="  MiXed@Example.com ", name="A", mobile="9000000005")
    assert User.objects.get().email == "mixed@example.com"
    with pytest.raises(IntegrityError):
        User.objects.create_user(email="mixed@example.com", name="B", mobile="9000000006")


def test_superuser_requires_password_and_flags():
    with pytest.raises(ValueError):
        User.objects.create_superuser(email="a@b.com", name="A", mobile="9000000007")
    admin = User.objects.create_superuser(
        email="a@b.com", name="A", password="s3cret-pass!", mobile="9000000008"
    )
    assert admin.is_staff and admin.is_superuser and admin.check_password("s3cret-pass!")


def test_purge_command_removes_only_stale_rows():
    user = User.objects.create_user(email="u@b.com", name="U", mobile="9000000009")
    now = timezone.now()
    expired = AuthToken.objects.create(user=user, expires_at=now - timedelta(days=1))
    live = AuthToken.objects.create(user=user, expires_at=now + timedelta(days=1))
    OtpChallenge.objects.create(
        email="u@b.com", purpose="login", code_hash="x", expires_at=now - timedelta(days=2)
    )
    call_command("purge_expired_auth")
    assert list(AuthToken.objects.values_list("pk", flat=True)) == [live.pk] and expired.pk != live.pk
    assert not OtpChallenge.objects.exists()


def test_mobile_is_required_and_unique():
    User.objects.create_user(email="a@x.com", name="A", mobile="9876543210")
    for missing in ({}, {"mobile": ""}, {"mobile": "   "}):
        with pytest.raises(ValueError, match="Mobile"):
            User.objects.create_user(email="n@x.com", name="N", **missing)
    with pytest.raises(IntegrityError):
        User.objects.create_user(email="d@x.com", name="D", mobile="9876543210")


def test_database_rejects_blank_mobile_even_without_the_manager():
    with pytest.raises(IntegrityError):
        User(email="raw@x.com", name="Raw", mobile="").save()


def test_validation_rejects_blank_and_malformed_mobile():
    from django.core.exceptions import ValidationError

    for bad in ("", "12345", "abcdefghij"):
        with pytest.raises(ValidationError) as exc:
            User(email="v@x.com", name="V", mobile=bad).full_clean(exclude=["password"])
        assert "mobile" in exc.value.message_dict


def test_mobile_is_stripped_before_saving():
    user = User.objects.create_user(email="s@x.com", name="S", mobile=" 9876543210 ")
    assert user.mobile == "9876543210"


def test_full_clean_reports_duplicate_mobile_clearly():
    from django.core.exceptions import ValidationError

    User.objects.create_user(email="a@x.com", name="A", mobile="9876543210")
    duplicate = User(email="b@x.com", name="B", mobile="9876543210")
    with pytest.raises(ValidationError, match="mobile number already exists"):
        duplicate.full_clean(exclude=["password"])
