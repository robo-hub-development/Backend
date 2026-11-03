from django.contrib import admin

from .models import CartItem


@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    """Read-only support view: staff can see what a customer has in their cart, not edit it."""

    list_display = ("user", "product", "quantity", "updated_at")
    list_select_related = ("user", "product")
    search_fields = ("user__email", "product__name")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
