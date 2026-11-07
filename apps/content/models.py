import math

from django.core.validators import FileExtensionValidator
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone

from apps.catalog.validators import media_type_for, validate_media_file
from apps.common.models import AuditModel
from apps.common.richtext import clean_html, plain_text

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


WORDS_PER_MINUTE = 200  # reading speed behind `read_minutes`
EXCERPT_LENGTH = 160


class ArticleCategory(AuditModel):
    """Master list of blog categories. Staff manage it once; articles pick from it (so labels and links stay
    consistent instead of drifting through free text)."""

    name = models.CharField(max_length=60)
    slug = models.SlugField(
        max_length=80, unique=True, help_text="Used to filter the blog: /blog?category=<slug>."
    )
    sort_order = models.PositiveSmallIntegerField(default=0, help_text="Lowest number is shown first.")
    is_active = models.BooleanField(
        default=True, help_text="Inactive categories are hidden from the category list."
    )

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "article categories"
        constraints = [
            # "Guides" and "guides" are the same category.
            models.UniqueConstraint(Lower("name"), name="articlecategory_name_ci_unique"),
        ]

    def __str__(self):
        return self.name


class Article(AuditModel):
    """A blog post. It is public only while `is_published` is on and `published_at` has arrived, so staff can
    keep drafts, publish now, or schedule a post by setting a future date."""

    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, help_text="Used in the address: /blog/<slug>.")
    excerpt = models.CharField(
        max_length=300,
        blank=True,
        help_text="Short summary for cards and search results. Filled from the text if empty.",
    )
    body = models.TextField(
        verbose_name="content", help_text="The article text (formatting is cleaned on save)."
    )
    media = models.FileField(
        upload_to="articles/",
        validators=[validate_media_file],
        help_text="The main image or video (jpg, png, webp, gif, mp4, webm); its type is detected.",
    )
    media_type = models.CharField(
        max_length=5, choices=[("image", "Image"), ("video", "Video")], default="image", editable=False
    )
    category = models.ForeignKey(
        ArticleCategory,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="articles",
        help_text="Pick from the article categories (manage them under Article categories).",
    )
    author = models.CharField(max_length=100, blank=True, help_text="Shown under the title. Defaults to you.")
    is_published = models.BooleanField(default=False, help_text="Off keeps the article a draft (not public).")
    published_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When it goes live. Empty = now, on publishing. A future time schedules it.",
    )
    read_minutes = models.PositiveSmallIntegerField(default=1, editable=False)

    class Meta:
        ordering = ["-published_at", "-created_at"]
        indexes = [models.Index(fields=["is_published", "-published_at"], name="article_pub_date_idx")]
        constraints = [
            models.CheckConstraint(
                condition=Q(is_published=False) | Q(published_at__isnull=False),
                name="article_published_has_date",
            ),
        ]

    def __str__(self):
        return self.title

    def clean(self):
        super().clean()
        # Forms validate the constraint below before save() runs, so the default publish time is set here too.
        if self.is_published and self.published_at is None:
            self.published_at = timezone.now()

    def save(self, *args, **kwargs):
        # Derived fields are set here so every writer (admin, shell, scripts) ends up with consistent rows.
        self.media_type = media_type_for(self.media.name)
        self.body = clean_html(self.body)
        text = plain_text(self.body)
        self.read_minutes = max(1, math.ceil(len(text.split()) / WORDS_PER_MINUTE))
        if not self.excerpt.strip():
            self.excerpt = text[:EXCERPT_LENGTH].rsplit(" ", 1)[0] if len(text) > EXCERPT_LENGTH else text
        if self.is_published and self.published_at is None:
            self.published_at = timezone.now()
        super().save(*args, **kwargs)
