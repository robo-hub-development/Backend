from django.contrib import admin

from .models import Cart, CartItem


class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0
    can_delete = False
    fields = ("product", "quantity", "ordered_at")
    readonly_fields = fields

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("product")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    """Read-only support view: staff can see a customer's cart (live and ordered lines), not edit it."""

    list_display = ("user", "updated_at")
    list_select_related = ("user",)
    search_fields = ("user__email",)
    readonly_fields = ("user", "created_at", "updated_at")
    inlines = [CartItemInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
