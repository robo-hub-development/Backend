from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.accounts.models import User

pytestmark = pytest.mark.django_db
PASSWORD = "Str0ng-admin-pass-42"


@pytest.fixture
def env(monkeypatch):
    values = {
        "DJANGO_SUPERUSER_EMAIL": "Boss@Example.com",
        "DJANGO_SUPERUSER_NAME": "Boss",
        "DJANGO_SUPERUSER_MOBILE": "9876501234",
        "DJANGO_SUPERUSER_PASSWORD": PASSWORD,
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return monkeypatch


def run() -> str:
    out = StringIO()
    call_command("ensure_superuser", stdout=out)
    return out.getvalue()


def test_creates_a_working_superuser_and_never_prints_the_password(env):
    output = run()
    admin = User.objects.get(email="boss@example.com")
    assert admin.is_superuser and admin.is_staff and admin.check_password(PASSWORD)
    assert admin.mobile == "9876501234" and PASSWORD not in output


def test_is_idempotent_and_keeps_the_existing_password(env):
    run()
    env.setenv("DJANGO_SUPERUSER_PASSWORD", "Another-Str0ng-pass-77")
    assert "already exists" in run()
    assert User.objects.count() == 1
    assert User.objects.get().check_password(PASSWORD)


def test_does_nothing_when_email_not_set(monkeypatch):
    monkeypatch.delenv("DJANGO_SUPERUSER_EMAIL", raising=False)
    assert "skipping" in run()
    assert not User.objects.exists()


@pytest.mark.parametrize("missing", ["NAME", "MOBILE", "PASSWORD"])
def test_reports_missing_variables(env, missing):
    env.delenv(f"DJANGO_SUPERUSER_{missing}")
    with pytest.raises(CommandError, match=missing):
        run()
    assert not User.objects.exists()


def test_rejects_weak_password_and_bad_mobile(env):
    env.setenv("DJANGO_SUPERUSER_PASSWORD", "12345678")
    with pytest.raises(CommandError):
        run()
    env.setenv("DJANGO_SUPERUSER_PASSWORD", PASSWORD)
    env.setenv("DJANGO_SUPERUSER_MOBILE", "abc")
    with pytest.raises(CommandError, match="mobile"):
        run()
    assert not User.objects.exists()


def test_rejects_a_mobile_already_in_use(env):
    User.objects.create_user(email="other@example.com", name="O", mobile="9876501234")
    with pytest.raises(CommandError, match="mobile"):
        run()
