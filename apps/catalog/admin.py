from django.contrib import admin
from django.db.models import Prefetch
from django.utils.html import format_html

from apps.common.admin import AuditAdminMixin, RichTextAdminMixin

from .models import Category, Product, ProductMedia, ProductVariant, VariantOption


@admin.register(Category)
class CategoryAdmin(RichTextAdminMixin, AuditAdminMixin, admin.ModelAdmin):
    rich_text_fields = ("description",)
    list_display = ("name", "parent", "sort_order", "is_active")
    list_filter = ("is_active", "parent")
    list_select_related = ("parent",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


class ProductMediaInline(admin.TabularInline):
    model = ProductMedia
    extra = 1

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("product")


@admin.register(VariantOption)
class VariantOptionAdmin(AuditAdminMixin, admin.ModelAdmin):
    list_display = ("title", "type", "slug")
    list_filter = ("type",)
    search_fields = ("title", "slug")
    prepopulated_fields = {"slug": ("title",)}


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 0
    autocomplete_fields = ("option",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("option", "product")


@admin.register(Product)
class ProductAdmin(RichTextAdminMixin, AuditAdminMixin, admin.ModelAdmin):
    rich_text_fields = ("description",)
    list_display = ("image", "name", "category", "price", "stock_quantity", "is_active", "is_new")
    list_display_links = ("image", "name")
    list_filter = ("is_active", "is_new", "category")
    list_select_related = ("category",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [ProductMediaInline, ProductVariantInline]
    # One section, no headings. The short fields share a wrapping three-column grid; the summary and the
    # description (with its editor) get a full-width row each; the read-only audit fields close the form.
    fieldsets = (
        (
            None,
            {
                "fields": (
                    (
                        "name",
                        "slug",
                        "category",
                        "price",
                        "compare_at_price",
                        "stock_quantity",
                        "variant_label",
                        "rating",
                        "review_count",
                        "sold_count",
                        "is_active",
                        "is_new",
                    ),
                    "summary",
                    "description",
                    ("created_at", "updated_at", "created_by", "modified_by"),
                )
            },
        ),
    )

    def get_queryset(self, request):
        # One extra query for the whole page instead of one per row: the product images, main one first.
        images = ProductMedia.objects.filter(media_type=ProductMedia.MediaType.IMAGE)
        return (
            super().get_queryset(request).prefetch_related(Prefetch("media", images, to_attr="list_images"))
        )

    @admin.display(description="Image")
    def image(self, obj):
        first = obj.list_images[0] if obj.list_images else None  # lowest position = the main image
        if first is None:
            return "-"
        return format_html(
            '<img src="{}" alt="{}" width="56" height="56" loading="lazy" '
            'style="object-fit:cover;border-radius:8px;border:1px solid var(--border-color)">',
            first.file.url,
            first.alt or obj.name,
        )
