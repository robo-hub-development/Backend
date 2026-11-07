from django.contrib import admin, messages
from django.db.models import Count
from django.utils import timezone
from django.utils.html import format_html

from apps.common.admin import AuditAdminMixin, RichTextAdminMixin

from .models import Article, ArticleCategory, Banner


@admin.register(Banner)
class BannerAdmin(AuditAdminMixin, admin.ModelAdmin):
    list_display = ("title", "eyebrow", "sort_order", "is_active", "updated_at")
    list_editable = ("sort_order", "is_active")
    list_filter = ("is_active",)
    search_fields = ("title", "eyebrow")
    fieldsets = (
        (None, {"fields": ("eyebrow", "title", "description", "is_active", "sort_order")}),
        ("Image", {"fields": ("image", "image_alt")}),
        ("Buttons", {"fields": ("primary_cta", "secondary_cta")}),
    )


@admin.register(ArticleCategory)
class ArticleCategoryAdmin(AuditAdminMixin, admin.ModelAdmin):
    list_display = ("name", "slug", "sort_order", "is_active", "article_total")
    list_editable = ("sort_order", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")  # also what the article form's category picker searches
    prepopulated_fields = {"slug": ("name",)}

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_articles=Count("articles"))

    @admin.display(description="Articles", ordering="_articles")
    def article_total(self, obj):
        return obj._articles


@admin.register(Article)
class ArticleAdmin(RichTextAdminMixin, AuditAdminMixin, admin.ModelAdmin):
    rich_text_fields = ("body",)
    list_display = (
        "thumbnail",
        "title",
        "category",
        "author",
        "is_published",
        "published_at",
        "read_minutes",
    )
    list_display_links = ("thumbnail", "title")
    list_editable = ("is_published",)
    list_filter = ("is_published", "category")
    list_select_related = ("category",)
    autocomplete_fields = ("category",)
    search_fields = ("title", "excerpt", "author", "category__name")
    prepopulated_fields = {"slug": ("title",)}
    actions = ["publish_articles", "unpublish_articles"]
    fieldsets = (
        (None, {"fields": ("title", "slug", "excerpt", "body")}),
        ("Media", {"fields": ("media",)}),
        ("Details", {"fields": ("category", "author")}),
        ("Publishing", {"fields": ("is_published", "published_at")}),
    )

    @admin.display(description="Media")
    def thumbnail(self, obj):
        if obj.media_type == "video":
            return "Video"
        return format_html(
            '<img src="{}" alt="" width="72" height="48" loading="lazy" '
            'style="object-fit:cover;border-radius:6px;border:1px solid var(--border-color)">',
            obj.media.url,
        )

    def save_model(self, request, obj, form, change):
        if not obj.author.strip():
            obj.author = request.user.name  # the writer is the author unless staff say otherwise
        super().save_model(request, obj, form, change)

    @admin.action(description="Publish the selected articles (now, unless a date is already set)")
    def publish_articles(self, request, queryset):
        queryset.filter(published_at__isnull=True).update(published_at=timezone.now())
        count = queryset.exclude(is_published=True).update(is_published=True, updated_at=timezone.now())
        self.message_user(request, f"{count} article(s) published.", messages.SUCCESS)

    @admin.action(description="Unpublish the selected articles (back to draft)")
    def unpublish_articles(self, request, queryset):
        count = queryset.filter(is_published=True).update(is_published=False, updated_at=timezone.now())
        self.message_user(request, f"{count} article(s) unpublished.", messages.SUCCESS)
