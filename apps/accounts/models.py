from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone

from apps.common.models import TimeStampedModel, UUIDModel

from .validators import validate_mobile


class UserManager(BaseUserManager):
    use_in_migrations = True

    @staticmethod
    def normalize(email: str) -> str:
        return email.strip().lower()

    def _create(self, email, password, **extra):
        if not email:
            raise ValueError("Email is required.")
        if not str(extra.get("mobile", "")).strip():
            raise ValueError("Mobile number is required.")
        user = self.model(email=self.normalize(email), **extra)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()  # customers authenticate with OTP only
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        if not password:
            raise ValueError("Superusers need a password.")
        return self._create(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin, TimeStampedModel):
    """Storefront customer or staff member. Email is the login identifier (always stored lower-case)."""

    email = models.EmailField(unique=True)
    name = models.CharField(max_length=150)
    mobile = models.CharField(
        max_length=16,
        unique=True,
        validators=[validate_mobile],
        error_messages={"unique": "A user with this mobile number already exists."},
    )
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["name", "mobile"]

    class Meta:
        constraints = [
            models.CheckConstraint(condition=~models.Q(mobile=""), name="user_mobile_not_empty"),
        ]

    def save(self, *args, **kwargs):
        self.email = UserManager.normalize(self.email)
        self.mobile = self.mobile.strip()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.email


class OtpChallenge(UUIDModel):
    """One emailed one-time code. Only a keyed hash of the code is stored."""

    class Purpose(models.TextChoices):
        LOGIN = "login"
        REGISTER = "register"

    email = models.EmailField()
    purpose = models.CharField(max_length=10, choices=Purpose.choices)
    code_hash = models.CharField(max_length=64)
    # Registration details are held here until the OTP is verified; no user exists before that.
    name = models.CharField(max_length=150, blank=True)
    mobile = models.CharField(max_length=16, blank=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["email", "purpose", "-created_at"])]

    def __str__(self):
        return f"{self.purpose}:{self.email}"


class AuthToken(UUIDModel):
    """Server-side session behind a JWT (its id is the token's `jti`); revoking it logs the user out."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="auth_tokens")
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["expires_at"])]

    def __str__(self):
        return f"token:{self.user_id}"

    @property
    def is_valid(self) -> bool:
        return self.revoked_at is None and self.expires_at > timezone.now()
