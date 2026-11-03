from datetime import timedelta
from unittest import mock

import pytest
from django.core import mail
from django.utils import timezone
from rest_framework.throttling import SimpleRateThrottle

from apps.accounts.models import AuthToken, OtpChallenge, User
from apps.common.tests import prod_settings

from .conftest import SEND, VERIFY, last_code, register

pytestmark = pytest.mark.django_db


def send(api, **body):
    return api.post(SEND, body, format="json")


def verify(api, **body):
    return api.post(VERIFY, body, format="json")


class TestSend:
    def test_login_sends_code_and_stores_only_a_hash(self, api, user):
        response = send(api, purpose="login", email="Aarav@Example.com")
        assert response.status_code == 200
        assert response.data == {"expiresInSeconds": 120}
        assert len(mail.outbox) == 1
        challenge = OtpChallenge.objects.get()
        assert last_code() not in challenge.code_hash
        assert challenge.email == "aarav@example.com"

    def test_login_unknown_email_rejected(self, api):
        response = send(api, purpose="login", email="nobody@example.com")
        assert response.status_code == 400
        assert "email" in response.data["errors"]
        assert not mail.outbox

    def test_register_existing_email_rejected(self, api, user):
        response = send(api, purpose="register", email=user.email, name="X", mobile="9876543210")
        assert response.status_code == 400
        assert "email" in response.data["errors"]

    @pytest.mark.parametrize("missing", ["name", "mobile"])
    def test_register_requires_name_and_mobile(self, api, missing):
        body = {"purpose": "register", "email": "a@b.com", "name": "A", "mobile": "9876543210"}
        body.pop(missing)
        response = send(api, **body)
        assert response.status_code == 400
        assert missing in response.data["errors"]

    def test_invalid_mobile_and_email_and_purpose(self, api):
        assert (
            "mobile" in send(api, purpose="register", email="a@b.com", name="A", mobile="12").data["errors"]
        )
        assert "email" in send(api, purpose="login", email="not-an-email").data["errors"]
        assert "purpose" in send(api, purpose="hack", email="a@b.com").data["errors"]

    def test_new_code_invalidates_previous(self, api, user):
        send(api, purpose="login", email=user.email)
        send(api, purpose="login", email=user.email)
        assert OtpChallenge.objects.count() == 2
        assert OtpChallenge.objects.filter(consumed_at__isnull=True).count() == 1

    def test_resend_cooldown(self, api, user, settings):
        settings.OTP_RESEND_COOLDOWN_SECONDS = 30
        assert send(api, purpose="login", email=user.email).status_code == 200
        response = send(api, purpose="login", email=user.email)
        assert response.status_code == 429
        assert "Retry-After" in response

    def test_email_failure_returns_503_and_leaves_no_challenge(self, api, user):
        with mock.patch("apps.accounts.services.send_mail", side_effect=OSError("smtp down")):
            response = send(api, purpose="login", email=user.email)
        assert response.status_code == 503
        assert not OtpChallenge.objects.exists()

    def test_ip_throttle(self, api, user):
        from django.core.cache import cache

        cache.clear()
        with mock.patch.dict(SimpleRateThrottle.THROTTLE_RATES, {"otp_send": "2/min"}):
            codes = [send(api, purpose="login", email=user.email).status_code for _ in range(3)]
        cache.clear()
        assert codes == [200, 200, 429]


class TestVerify:
    def test_login_success_returns_token_and_user(self, api, user):
        send(api, purpose="login", email=user.email)
        response = verify(api, purpose="login", email=user.email, otp=last_code())
        assert response.status_code == 200
        assert response.data["user"] == {
            "id": str(user.pk),
            "name": "Aarav Mehta",
            "email": user.email,
            "mobile": "9876543210",
        }
        assert response.data["expiresInSeconds"] == 604800
        assert response.data["token"].count(".") == 2  # a JWT: header.payload.signature
        assert AuthToken.objects.count() == 1

    def test_register_creates_user_with_unusable_password(self, api):
        response = register(api)
        assert response.status_code == 200
        created = User.objects.get(email="new@example.com")
        assert created.name == "New User" and not created.has_usable_password()
        assert not created.is_staff

    def test_wrong_code_counts_attempts_then_locks(self, api, user, settings):
        send(api, purpose="login", email=user.email)
        good = last_code()
        bad = "000000" if good != "000000" else "111111"
        for _ in range(settings.OTP_MAX_ATTEMPTS):
            response = verify(api, purpose="login", email=user.email, otp=bad)
            assert response.status_code == 400 and "otp" in response.data["errors"]
        assert OtpChallenge.objects.get().attempts == settings.OTP_MAX_ATTEMPTS
        locked = verify(api, purpose="login", email=user.email, otp=good)
        assert locked.status_code == 400  # even the correct code is refused after lockout

    def test_expired_code(self, api, user):
        send(api, purpose="login", email=user.email)
        OtpChallenge.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        assert verify(api, purpose="login", email=user.email, otp=last_code()).status_code == 400

    def test_code_is_single_use(self, api, user):
        send(api, purpose="login", email=user.email)
        code = last_code()
        assert verify(api, purpose="login", email=user.email, otp=code).status_code == 200
        assert verify(api, purpose="login", email=user.email, otp=code).status_code == 400

    def test_code_is_bound_to_purpose_and_email(self, api, user):
        send(api, purpose="login", email=user.email)
        code = last_code()
        assert verify(api, purpose="register", email=user.email, otp=code).status_code == 400
        assert verify(api, purpose="login", email="other@example.com", otp=code).status_code == 400

    def test_malformed_otp(self, api, user):
        assert "otp" in verify(api, purpose="login", email=user.email, otp="12ab").data["errors"]

    def test_register_race_when_email_taken_after_send(self, api):
        send(api, purpose="register", email="race@example.com", name="R", mobile="9876543210")
        code = last_code()
        User.objects.create_user(email="race@example.com", name="Other", mobile="9000000004")
        response = verify(api, purpose="register", email="race@example.com", otp=code)
        assert response.status_code == 400 and "email" in response.data["errors"]
        assert User.objects.filter(email="race@example.com").count() == 1

    def test_inactive_user_cannot_login(self, api, user):
        send(api, purpose="login", email=user.email)
        code = last_code()
        User.objects.filter(pk=user.pk).update(is_active=False)
        assert verify(api, purpose="login", email=user.email, otp=code).status_code == 400


class TestUniqueMobile:
    def test_register_with_taken_mobile_rejected_before_sending_code(self, api, user):
        response = send(api, purpose="register", email="new@x.com", name="N", mobile=user.mobile)
        assert response.status_code == 400
        assert set(response.data["errors"]) == {"mobile"}
        assert not mail.outbox

    def test_both_identifiers_taken_reports_both(self, api, user):
        response = send(api, purpose="register", email=user.email, name="N", mobile=user.mobile)
        assert set(response.data["errors"]) == {"email", "mobile"}

    def test_mobile_with_surrounding_spaces_still_detected(self, api, user):
        response = send(api, purpose="register", email="n@x.com", name="N", mobile=f" {user.mobile} ")
        assert "mobile" in response.data["errors"]

    def test_race_mobile_taken_after_code_sent(self, api):
        send(api, purpose="register", email="late@x.com", name="L", mobile="9123456789")
        code = last_code()
        User.objects.create_user(email="other@x.com", name="O", mobile="9123456789")
        response = verify(api, purpose="register", email="late@x.com", otp=code)
        assert response.status_code == 400 and set(response.data["errors"]) == {"mobile"}
        assert not User.objects.filter(email="late@x.com").exists()


class TestFixedDevelopmentCode:
    def test_fixed_code_is_used_when_configured(self, api, user, settings):
        settings.OTP_FIXED_CODE = "123456"
        send(api, purpose="login", email=user.email)
        assert last_code() == "123456"
        assert verify(api, purpose="login", email=user.email, otp="123456").status_code == 200

    def test_fixed_code_still_obeys_expiry_single_use_and_binding(self, api, user, settings):
        settings.OTP_FIXED_CODE = "123456"
        send(api, purpose="login", email=user.email)
        assert verify(api, purpose="register", email=user.email, otp="123456").status_code == 400
        assert verify(api, purpose="login", email=user.email, otp="123456").status_code == 200
        assert verify(api, purpose="login", email=user.email, otp="123456").status_code == 400

    def test_codes_are_random_when_not_configured(self, api, user):
        codes = set()
        for _ in range(5):
            OtpChallenge.objects.all().delete()
            send(api, purpose="login", email=user.email)
            codes.add(last_code())
        assert len(codes) > 1

    def test_production_ignores_the_ordinary_variable_and_defaults_to_random(self):
        assert prod_settings.load("OTP_FIXED_CODE", OTP_FIXED_CODE="123456") == {"OTP_FIXED_CODE": ""}
        assert prod_settings.load("OTP_FIXED_CODE") == {"OTP_FIXED_CODE": ""}

    def test_production_fixed_code_needs_the_explicit_insecure_variable(self):
        assert prod_settings.load("OTP_FIXED_CODE", INSECURE_FIXED_OTP="123456") == {
            "OTP_FIXED_CODE": "123456"
        }

    @pytest.mark.parametrize("bad", ["12345", "1234567", "abcdef", "12 456"])
    def test_production_rejects_a_malformed_fixed_code(self, bad):
        assert prod_settings.load("OTP_FIXED_CODE", INSECURE_FIXED_OTP=bad) == {
            "error": "ImproperlyConfigured"
        }

    def test_deploy_check_warns_while_a_fixed_code_is_active(self, settings):
        from apps.common.checks import fixed_otp_is_off

        settings.DEBUG = False
        settings.OTP_FIXED_CODE = "123456"
        assert [w.id for w in fixed_otp_is_off(None)] == ["robohub.W002"]
        settings.OTP_FIXED_CODE = ""
        assert fixed_otp_is_off(None) == []


class TestProductionEmailSettings:
    def test_boots_without_email_and_falls_back_to_console(self):
        loaded = prod_settings.load("EMAIL_BACKEND", "DEFAULT_FROM_EMAIL")
        assert loaded["EMAIL_BACKEND"].endswith("console.EmailBackend")
        assert loaded["DEFAULT_FROM_EMAIL"] == "no-reply@robohub.local"

    def test_uses_smtp_when_host_is_set(self):
        loaded = prod_settings.load(
            "EMAIL_BACKEND",
            "DEFAULT_FROM_EMAIL",
            EMAIL_HOST="smtp.example.com",
            DEFAULT_FROM_EMAIL="a@example.com",
        )
        assert loaded["EMAIL_BACKEND"].endswith("smtp.EmailBackend")
        assert loaded["DEFAULT_FROM_EMAIL"] == "a@example.com"

    def test_deploy_check_warns_when_email_is_console_in_production(self, settings):
        from apps.common.checks import email_is_configured

        settings.DEBUG = False
        settings.EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
        assert [w.id for w in email_is_configured(None)] == ["robohub.W001"]
        settings.EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
        assert email_is_configured(None) == []
