from django.contrib import admin

from apps.common.admin import AuditAdminMixin

from .models import Category, Product, ProductMedia, ProductVariant, VariantOption


@admin.register(Category)
class CategoryAdmin(AuditAdminMixin, admin.ModelAdmin):
    list_display = ("name", "parent", "sort_order", "is_active")
    list_filter = ("is_active", "parent")
    list_select_related = ("parent",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


class ProductMediaInline(admin.TabularInline):
    model = ProductMedia
    extra = 1


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


@admin.register(Product)
class ProductAdmin(AuditAdminMixin, admin.ModelAdmin):
    list_display = ("name", "category", "price", "stock_quantity", "is_active", "is_new")
    list_filter = ("is_active", "is_new", "category")
    list_select_related = ("category",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [ProductMediaInline, ProductVariantInline]
