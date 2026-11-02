"""Auth business logic: OTP issue/verify and session tokens. No HTTP objects in here."""

import hashlib
import hmac
import logging
import secrets
import uuid
from datetime import timedelta

import jwt
from django.conf import settings
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, Throttled, ValidationError

from .models import AuthToken, OtpChallenge, User, UserManager

logger = logging.getLogger(__name__)

OTP_LENGTH = 6
INVALID_OTP = "That code is incorrect or has expired."
EMAIL_TAKEN = "This email is already registered. Please log in."
MOBILE_TAKEN = "This mobile number is already registered."


class EmailDeliveryError(APIException):
    status_code = 503
    default_detail = "We could not send the code right now. Please try again shortly."


def _hash_code(email: str, purpose: str, code: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode(), f"{email}:{purpose}:{code}".encode(), hashlib.sha256
    ).hexdigest()


def _new_code() -> str:
    """Random 6-digit code, or the fixed development code when OTP_FIXED_CODE is set (never in production)."""
    fixed = settings.OTP_FIXED_CODE
    if fixed:
        logger.warning("OTP_FIXED_CODE is active: every OTP is %s. Development only.", fixed)
        return fixed
    return "".join(secrets.choice("0123456789") for _ in range(OTP_LENGTH))


def send_otp(*, purpose: str, email: str, name: str = "", mobile: str = "") -> int:
    """Validate the request against account state, email a fresh code, return its lifetime in seconds."""
    email = UserManager.normalize(email)
    existing = User.objects.filter(email=email).first()
    if purpose == OtpChallenge.Purpose.REGISTER:
        taken = {}
        if existing:
            taken["email"] = EMAIL_TAKEN
        if mobile and User.objects.filter(mobile=mobile).exists():
            taken["mobile"] = MOBILE_TAKEN
        if taken:
            raise ValidationError(taken)
    if purpose == OtpChallenge.Purpose.LOGIN and (existing is None or not existing.is_active):
        raise ValidationError({"email": "No account found for this email. Please register."})

    now = timezone.now()
    latest = OtpChallenge.objects.filter(email=email, purpose=purpose).order_by("-created_at").first()
    cooldown = settings.OTP_RESEND_COOLDOWN_SECONDS
    if latest and (now - latest.created_at).total_seconds() < cooldown:
        wait = max(1, int(cooldown - (now - latest.created_at).total_seconds()))
        raise Throttled(
            wait=wait, detail="Please wait before requesting another code."
        )  # DRF appends the seconds

    code = _new_code()
    with transaction.atomic():
        # Only the newest code is ever valid.
        OtpChallenge.objects.filter(email=email, purpose=purpose, consumed_at__isnull=True).update(
            consumed_at=now
        )
        challenge = OtpChallenge.objects.create(
            email=email,
            purpose=purpose,
            code_hash=_hash_code(email, purpose, code),
            name=name,
            mobile=mobile,
            expires_at=now + timedelta(seconds=settings.OTP_TTL_SECONDS),
        )
    try:
        send_mail(
            subject="Your Stem Robo Hub verification code",
            message=f"Your code is {code}. It expires in {settings.OTP_TTL_SECONDS // 60} minutes. "
            "If you did not request it, ignore this email.",
            from_email=None,
            recipient_list=[email],
        )
    except Exception:
        logger.exception("OTP email failed for challenge %s", challenge.pk)
        challenge.delete()
        raise EmailDeliveryError from None
    return settings.OTP_TTL_SECONDS


def verify_otp(*, purpose: str, email: str, otp: str) -> tuple[User, str, int]:
    """Check the code; return (user, raw_token, token_ttl_seconds). Registers the user if needed."""
    email = UserManager.normalize(email)
    failure = None
    with transaction.atomic():
        challenge = (
            OtpChallenge.objects.select_for_update()
            .filter(email=email, purpose=purpose, consumed_at__isnull=True)
            .order_by("-created_at")
            .first()
        )
        now = timezone.now()
        if challenge is None or challenge.expires_at <= now:
            failure = INVALID_OTP
        elif challenge.attempts >= settings.OTP_MAX_ATTEMPTS:
            failure = "Too many incorrect attempts. Please request a new code."
        else:
            challenge.attempts += 1
            if hmac.compare_digest(challenge.code_hash, _hash_code(email, purpose, otp)):
                challenge.consumed_at = now
                challenge.save(update_fields=["attempts", "consumed_at"])
                user = _resolve_user(challenge)  # may raise; rolls back the consumption
            else:
                challenge.save(update_fields=["attempts"])  # persisted: failure is raised after commit
                failure = INVALID_OTP
    if failure:
        raise ValidationError({"otp": failure})

    user.last_login = timezone.now()
    user.save(update_fields=["last_login"])
    token, ttl = issue_token(user)
    return user, token, ttl


def _resolve_user(challenge: OtpChallenge) -> User:
    if challenge.purpose == OtpChallenge.Purpose.REGISTER:
        try:
            with transaction.atomic():
                return User.objects.create_user(
                    email=challenge.email, name=challenge.name, mobile=challenge.mobile
                )
        except IntegrityError:
            # Lost a race after the OTP was sent: report whichever identifier was taken meanwhile.
            taken = {}
            if User.objects.filter(email=challenge.email).exists():
                taken["email"] = EMAIL_TAKEN
            if challenge.mobile and User.objects.filter(mobile=challenge.mobile).exists():
                taken["mobile"] = MOBILE_TAKEN
            raise ValidationError(taken or {"email": EMAIL_TAKEN}) from None
    user = User.objects.filter(email=challenge.email, is_active=True).first()
    if user is None:
        raise ValidationError({"email": "No account found for this email. Please register."})
    return user


def issue_token(user: User) -> tuple[str, int]:
    """Create a session record and return a signed JWT bound to it (`jti` = session id)."""
    ttl = settings.AUTH_TOKEN_TTL_SECONDS
    issued = timezone.now()
    session = AuthToken.objects.create(user=user, expires_at=issued + timedelta(seconds=ttl))
    claims = {
        "iss": settings.JWT_ISSUER,
        "sub": str(user.pk),
        "jti": str(session.pk),
        "iat": issued,
        "exp": session.expires_at,
    }
    return jwt.encode(claims, settings.JWT_SIGNING_KEY, algorithm=settings.JWT_ALGORITHM), ttl


def find_valid_token(raw: str) -> AuthToken | None:
    """Verify signature, expiry and issuer, then confirm the session is not revoked and the user is active.

    The algorithm is pinned (never read from the token header) to rule out `alg=none` / key-confusion tricks.
    """
    try:
        claims = jwt.decode(
            raw,
            settings.JWT_SIGNING_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            issuer=settings.JWT_ISSUER,
            options={"require": ["exp", "iat", "iss", "sub", "jti"]},
        )
        session_id, user_id = uuid.UUID(claims["jti"]), uuid.UUID(claims["sub"])
    except (jwt.InvalidTokenError, ValueError):
        return None
    session = AuthToken.objects.select_related("user").filter(pk=session_id, user_id=user_id).first()
    if session and session.is_valid and session.user.is_active:
        return session
    return None


def revoke_token(token: AuthToken) -> None:
    if token.revoked_at is None:
        token.revoked_at = timezone.now()
        token.save(update_fields=["revoked_at"])
