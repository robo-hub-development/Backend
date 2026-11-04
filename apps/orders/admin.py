from django.contrib import admin

from .models import Order, OrderItem


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    fields = ("product_name", "unit_price", "quantity", "cart_item")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    """Read-only for now. Status changes must go through the transition map and restore stock on cancel,
    so they arrive as guarded actions in the admin/RBAC phase rather than as free-form field edits."""

    list_display = (
        "order_number",
        "user",
        "status",
        "payment_method",
        "payment_status",
        "total",
        "created_at",
    )
    list_filter = ("status", "payment_method", "payment_status")
    search_fields = ("user__email", "ship_full_name", "ship_mobile")
    list_select_related = ("user",)
    date_hierarchy = "created_at"
    inlines = [OrderItemInline]

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
