import uuid

from django.conf import settings
from django.db import models


class UUIDModel(models.Model):
    """Abstract base giving every model a non-guessable UUID primary key (no sequential IDs in URLs)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class TimeStampedModel(UUIDModel):
    """Default base for feature models: UUID primary key plus creation/update timestamps."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AuditModel(TimeStampedModel):
    """Timestamps plus who created / last modified the record.

    Use for staff-managed data (products, categories, banners, articles, orders...).
    Populated by AuditAdminMixin in the admin and by services that receive the acting user.
    SET_NULL keeps history when a staff account is removed; related_name="+" avoids
    reverse-accessor clutter on the user model.
    """

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    modified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        abstract = True
