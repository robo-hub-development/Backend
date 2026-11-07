import pytest
from django.core import mail
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError
from environ import Env

from config.settings.mail import CONSOLE_BACKEND, SMTP_BACKEND, email_settings


def settings_for(monkeypatch, **variables):
    for name in (
        "EMAIL_HOST", "EMAIL_PORT", "EMAIL_HOST_USER", "EMAIL_HOST_PASSWORD", "EMAIL_USE_TLS",
        "EMAIL_USE_SSL", "EMAIL_TIMEOUT", "DEFAULT_FROM_EMAIL", "SERVER_EMAIL",
    ):  # fmt: skip
        monkeypatch.delenv(name, raising=False)
    for name, value in variables.items():
        monkeypatch.setenv(name, value)
    return email_settings(Env(), "Stem Robo Hub")


def test_without_a_host_mail_goes_to_the_console(monkeypatch):
    conf = settings_for(monkeypatch)
    assert conf["EMAIL_BACKEND"] == CONSOLE_BACKEND
    assert conf["DEFAULT_FROM_EMAIL"] == "no-reply@robohub.local"


def test_a_host_switches_to_djangos_smtp_backend(monkeypatch):
    conf = settings_for(
        monkeypatch,
        EMAIL_HOST="smtp.gmail.com",
        EMAIL_HOST_USER="shop@gmail.com",
        EMAIL_HOST_PASSWORD="app-password",
    )
    assert conf["EMAIL_BACKEND"] == SMTP_BACKEND
    assert (conf["EMAIL_HOST"], conf["EMAIL_PORT"], conf["EMAIL_USE_TLS"], conf["EMAIL_USE_SSL"]) == (
        "smtp.gmail.com",
        587,
        True,
        False,
    )
    assert conf["EMAIL_HOST_PASSWORD"] == "app-password"
    assert conf["DEFAULT_FROM_EMAIL"] == "Stem Robo Hub <shop@gmail.com>"  # the authenticated account
    assert conf["EMAIL_TIMEOUT"] == 10


def test_ssl_uses_port_465_and_turns_tls_off(monkeypatch):
    conf = settings_for(monkeypatch, EMAIL_HOST="smtp.example.com", EMAIL_USE_SSL="true")
    assert (conf["EMAIL_PORT"], conf["EMAIL_USE_SSL"], conf["EMAIL_USE_TLS"]) == (465, True, False)


def test_tls_and_ssl_together_are_rejected(monkeypatch):
    with pytest.raises(ImproperlyConfigured, match="cannot both"):
        settings_for(monkeypatch, EMAIL_HOST="smtp.example.com", EMAIL_USE_SSL="true", EMAIL_USE_TLS="true")


def test_the_from_address_can_be_overridden(monkeypatch):
    conf = settings_for(
        monkeypatch, EMAIL_HOST="h", EMAIL_HOST_USER="a@b.io", DEFAULT_FROM_EMAIL="Shop <orders@shop.in>"
    )
    assert conf["DEFAULT_FROM_EMAIL"] == "Shop <orders@shop.in>"


def test_send_test_email_command(capsys):
    call_command("send_test_email", "owner@example.com")
    assert len(mail.outbox) == 1 and mail.outbox[0].to == ["owner@example.com"]
    out = capsys.readouterr().out
    assert "Test email sent to owner@example.com" in out and "password" not in out.lower()


def test_send_test_email_reports_smtp_errors_without_the_password(monkeypatch, settings):
    settings.EMAIL_HOST_PASSWORD = "super-secret"

    def boom(*args, **kwargs):
        raise OSError("Connection refused")

    monkeypatch.setattr("apps.notifications.management.commands.send_test_email.send_mail", boom)
    with pytest.raises(CommandError) as error:
        call_command("send_test_email", "owner@example.com")
    assert "OSError: Connection refused" in str(error.value) and "super-secret" not in str(error.value)
