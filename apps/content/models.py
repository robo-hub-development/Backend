from django.core.validators import FileExtensionValidator
from django.db import models

from apps.common.models import AuditModel

from .validators import validate_cta


class Banner(AuditModel):
    """Home-page hero slide. Slides show in `sort_order`; inactive ones are hidden from the API."""

    eyebrow = models.CharField(max_length=80, blank=True, help_text="Small label above the title.")
    title = models.CharField(max_length=160)
    description = models.CharField(max_length=300, blank=True)
    image = models.ImageField(
        upload_to="banners/", validators=[FileExtensionValidator(["jpg", "jpeg", "png", "webp"])]
    )
    image_alt = models.CharField(
        max_length=200, blank=True, help_text="Describes the image for screen readers."
    )
    primary_cta = models.JSONField(
        validators=[validate_cta],
        help_text='Main button, e.g. {"label": "Shop kits", "href": "/products?category=robot-kits"}',
    )
    secondary_cta = models.JSONField(
        null=True,
        blank=True,
        validators=[validate_cta],
        help_text='Optional second button, e.g. {"label": "Read guides", "href": "/blog"}',
    )
    sort_order = models.PositiveSmallIntegerField(default=0, help_text="Lowest number is shown first.")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "created_at", "id"]
        indexes = [models.Index(fields=["is_active", "sort_order"], name="banner_active_order_idx")]

    def __str__(self):
        return self.title
