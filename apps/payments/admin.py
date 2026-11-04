from django.contrib import admin

from .models import PaymentAttempt, PaymentItem, WebhookEvent


class PaymentItemInline(admin.TabularInline):
    model = PaymentItem
    extra = 0
    can_delete = False
    fields = ("product", "quantity", "unit_price", "cart_item")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(PaymentAttempt)
class PaymentAttemptAdmin(admin.ModelAdmin):
    """Read-only: payment state only changes through the gateway flow. Filter on "needs refund" to find
    payments that were taken but could not become orders; refund those in the Razorpay dashboard."""

    list_display = ("reference", "user", "status", "amount", "order", "failure_reason", "created_at")
    list_filter = ("status",)
    search_fields = ("reference", "user__email", "gateway_payment_id", "gateway_link_id")
    list_select_related = ("user", "order")
    inlines = [PaymentItemInline]

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ("event_type", "event_id", "created_at")
    search_fields = ("event_id",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
