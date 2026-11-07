from django.contrib import admin

from .models import Payment, PaymentTransaction, WebhookEvent


class PaymentTransactionInline(admin.TabularInline):
    model = PaymentTransaction
    extra = 0
    can_delete = False
    fields = ("kind", "amount", "gateway_ref", "reason", "initiated_by", "created_at")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    """Read-only: payment state only changes through the gateway flow and the order services. Filter on
    "needs refund" to find money that arrived but could not be used; refund it in the Razorpay dashboard."""

    list_display = (
        "reference",
        "order",
        "purpose",
        "method",
        "status",
        "amount",
        "failure_reason",
        "created_at",
    )
    list_filter = ("status", "purpose", "method")
    search_fields = ("reference", "order__user__email", "gateway_payment_id", "gateway_link_id")
    list_select_related = ("order",)
    inlines = [PaymentTransactionInline]

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields if f.name != "id"]  # no raw UUID row on the page

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
