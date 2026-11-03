from django.contrib import admin

from apps.common.admin import AuditAdminMixin

from .models import Banner


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
