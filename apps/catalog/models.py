from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q

from apps.common.models import AuditModel, TimeStampedModel

from .validators import media_type_for, validate_media_file

# Route segment used by GET /products/filters; a product slug must never shadow it.
RESERVED_PRODUCT_SLUGS = ("filters",)


class Category(AuditModel):
    """Two-level category tree (top-level -> sub-category). Products may attach at either level."""

    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True)
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to="categories/", blank=True)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="children"
    )
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["sort_order", "name"]
        constraints = [
            models.CheckConstraint(condition=~Q(parent=F("id")), name="category_not_own_parent"),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        if not self.parent_id:
            return
        if self.parent_id == self.id:
            raise ValidationError({"parent": "A category cannot be its own parent."})
        if self.parent.parent_id:
            raise ValidationError({"parent": "Categories can only be nested two levels deep."})
        if not self._state.adding and self.children.exists():
            raise ValidationError({"parent": "A category that has sub-categories must stay top-level."})

    def save(self, *args, **kwargs):
        self.clean()  # depth rule must hold for every write path, not only admin forms
        super().save(*args, **kwargs)

    @property
    def top_level(self) -> "Category":
        return self.parent if self.parent_id else self


class Product(AuditModel):
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True)
    summary = models.CharField(max_length=300, blank=True)
    description = models.TextField(blank=True)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    compare_at_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text="Original price; only set when the product is discounted (must exceed price).",
    )
    variant_label = models.CharField(max_length=50, default="Option", help_text="e.g. Edition, Pack, Bundle")
    stock_quantity = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    is_new = models.BooleanField(default=False)
    # Display values; there is no review feature yet, so staff/imports maintain them.
    rating = models.DecimalField(
        max_digits=2, decimal_places=1, default=0, validators=[MinValueValidator(0), MaxValueValidator(5)]
    )
    review_count = models.PositiveIntegerField(default=0)
    sold_count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(price__gte=0), name="product_price_non_negative"),
            models.CheckConstraint(
                condition=Q(compare_at_price__isnull=True) | Q(compare_at_price__gt=F("price")),
                name="product_compare_at_above_price",
            ),
            models.CheckConstraint(condition=Q(rating__gte=0, rating__lte=5), name="product_rating_0_to_5"),
            models.CheckConstraint(
                condition=~Q(slug__in=RESERVED_PRODUCT_SLUGS), name="product_slug_not_reserved"
            ),
        ]
        indexes = [
            models.Index(fields=["category", "is_active"], name="product_category_active_idx"),
            models.Index(fields=["-sold_count"], name="product_sold_idx"),
            models.Index(fields=["price"], name="product_price_idx"),
            models.Index(fields=["-created_at"], name="product_created_idx"),
        ]

    def __str__(self):
        return self.name

    @property
    def in_stock(self) -> bool:
        return self.stock_quantity > 0


class ProductMedia(TimeStampedModel):
    """Gallery item for a product: an image or a video file (type is derived from the upload)."""

    class MediaType(models.TextChoices):
        IMAGE = "image"
        VIDEO = "video"

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="media")
    file = models.FileField(upload_to="products/", validators=[validate_media_file])
    media_type = models.CharField(
        max_length=5, choices=MediaType.choices, default=MediaType.IMAGE, editable=False
    )
    alt = models.CharField(max_length=200, blank=True, help_text="Description / title shown with the media.")
    position = models.PositiveSmallIntegerField(
        default=0, help_text="Lowest position of an image is the main image."
    )

    class Meta:
        ordering = ["position", "created_at"]
        verbose_name_plural = "product media"

    def __str__(self):
        return f"{self.product_id}#{self.position}:{self.media_type}"

    def save(self, *args, **kwargs):
        self.media_type = media_type_for(self.file.name)
        super().save(*args, **kwargs)


class VariantOption(AuditModel):
    """Master list of reusable values (Size: Large, Color: Red); products map to it via ProductVariant."""

    class Type(models.TextChoices):
        SIZE = "size", "Size"
        COLOR = "color", "Color"

    title = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120)
    type = models.CharField(max_length=10, choices=Type.choices)

    class Meta:
        ordering = ["type", "title"]
        constraints = [
            models.UniqueConstraint(fields=["type", "slug"], name="variant_option_unique_slug_per_type"),
            models.UniqueConstraint(fields=["type", "title"], name="variant_option_unique_title_per_type"),
        ]

    def __str__(self):
        return f"{self.get_type_display()}: {self.title}"


class ProductVariant(TimeStampedModel):
    """A product's selectable option: maps a product to a VariantOption with its own price/stock.

    Catalogue-only: carts and orders reference the product, not the variant.
    """

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    option = models.ForeignKey(VariantOption, on_delete=models.PROTECT, related_name="product_variants")
    price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    compare_at_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)]
    )
    in_stock = models.BooleanField(default=True)
    image_index = models.PositiveSmallIntegerField(
        default=0, help_text="Index into the product's images (photos only, videos excluded)."
    )
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "created_at"]
        constraints = [
            models.UniqueConstraint(fields=["product", "option"], name="variant_unique_option_per_product"),
            models.CheckConstraint(condition=Q(price__gte=0), name="variant_price_non_negative"),
            models.CheckConstraint(
                condition=Q(compare_at_price__isnull=True) | Q(compare_at_price__gt=F("price")),
                name="variant_compare_at_above_price",
            ),
        ]

    def __str__(self):
        return f"{self.product_id}:{self.option_id}"
