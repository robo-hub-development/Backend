from django.conf import settings
from django.contrib import admin, messages
from django.http import Http404, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from rest_framework.exceptions import ValidationError

from apps.common.admin import AuditAdminMixin
from apps.notifications import services as notifications
from apps.notifications.models import EmailLog
from apps.payments.models import Payment

from . import checkout
from .invoice import invoice_context
from .invoice_pdf import render_invoice_pdf
from .models import CodTransitPolicy, Order, OrderCharge, OrderItem, OrderStatusEvent
from .services import InvalidTransition

# Staff status changes. Each one calls a service in orders.checkout, so the transition map, stock, cash,
# refunds and customer emails behave exactly as they do for the storefront; the admin only picks the step.
# key -> (label, statuses it is offered for, button style, confirmation text)
STATUS_ACTIONS = {
    "confirm": ("Accept order", {Order.Status.PENDING}, "primary", ""),
    "ship": ("Mark as shipped", {Order.Status.CONFIRMED}, "primary", ""),
    "deliver": ("Mark as delivered", {Order.Status.SHIPPED}, "primary", ""),
    "rto": (
        "Customer refused (return to origin)",
        {Order.Status.SHIPPED},
        "danger",
        "Mark this order as returned to origin? The COD fee is kept and the cash is never collected.",
    ),
    "cancel": (
        "Cancel order",
        {Order.Status.AWAITING_PAYMENT, Order.Status.PENDING, Order.Status.CONFIRMED},
        "danger",
        "Cancel this order? Stock is put back and anything paid online is refunded.",
    ),
}


class ReadOnlyInline(admin.TabularInline):
    extra = 0
    can_delete = False

    def get_readonly_fields(self, request, obj=None):
        return self.fields

    def has_add_permission(self, request, obj=None):
        return False


class OrderItemInline(ReadOnlyInline):
    model = OrderItem
    fields = ("product_name", "unit_price", "quantity", "cart_item")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("cart_item__product")


class OrderChargeInline(ReadOnlyInline):
    model = OrderCharge
    fields = ("kind", "label", "amount", "rule_summary")

    @admin.display(description="Rule applied")
    def rule_summary(self, obj):
        """The pricing rule snapshot in words ("Rs 40.00 - everywhere"), not raw JSON that contains an id."""
        rule = obj.rule or {}
        if not rule:
            return "-"
        amount = (
            f"{rule.get('value')}% of the items total"
            if rule.get("mode") == "percent"
            else f"Rs {rule.get('value')}"
        )
        where = {
            "state": f"state {rule.get('match')}",
            "pincode_prefix": f"pincodes starting {rule.get('match')}",
        }
        parts = [f"{amount} - {where.get(rule.get('scope'), 'everywhere')}"]
        if rule.get("min_fee") is not None:
            parts.append(f"min Rs {rule['min_fee']}")
        if rule.get("max_fee") is not None:
            parts.append(f"max Rs {rule['max_fee']}")
        return ", ".join(parts)


class OrderPaymentInline(ReadOnlyInline):
    model = Payment
    fields = ("reference", "purpose", "method", "status", "amount", "failure_reason")
    show_change_link = True


class OrderStatusEventInline(ReadOnlyInline):
    model = OrderStatusEvent
    fields = ("created_at", "from_status", "to_status", "actor", "reason")


class EmailLogInline(ReadOnlyInline):
    model = EmailLog
    fields = ("created_at", "kind", "recipient", "status", "attempts", "error")
    verbose_name_plural = "Emails sent to the customer"


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    """Read-only except the delivery partner and tracking number. Status changes must go through the
    transition map (and move stock and money), so they arrive as guarded actions in the admin/RBAC phase
    rather than as free-form field edits."""

    TRACKING_FIELDS = ("courier_name", "tracking_number")

    list_display = (
        "order_number",
        "user",
        "status",
        "payment_method",
        "payment_status",
        "total",
        "amount_paid",
        "tracking_number",
        "created_at",
        "invoice_link",
    )
    actions = ["print_invoices", "email_invoices", "accept_orders", "ship_orders", "deliver_orders"]
    list_filter = ("status", "payment_method", "payment_status")
    search_fields = ("user__email", "ship_full_name", "ship_mobile", "tracking_number")
    list_select_related = ("user",)
    date_hierarchy = "created_at"
    inlines = [OrderItemInline, OrderChargeInline, OrderPaymentInline, OrderStatusEventInline, EmailLogInline]
    # One section, no headings: every field sits in a single row-tuple, which the theme lays out as a
    # three-column grid that wraps by itself. The two editable fields come first.
    fieldsets = (
        (
            None,
            {
                "fields": (
                    (
                        "courier_name",
                        "tracking_number",
                        "number",
                        "user",
                        "cart",
                        "status",
                        "payment_method",
                        "payment_status",
                        "subtotal",
                        "charges_total",
                        "total",
                        "amount_paid",
                        "payment_due_by",
                        "cancelled_at",
                        "ship_full_name",
                        "ship_mobile",
                        "ship_line1",
                        "ship_line2",
                        "ship_city",
                        "ship_state",
                        "ship_pincode",
                        "created_at",
                        "updated_at",
                        "created_by",
                        "modified_by",
                    ),
                )
            },
        ),
    )

    # ---- invoices ---------------------------------------------------------------------------------------

    def get_urls(self):
        custom = [
            path(
                "<uuid:pk>/invoice/",
                self.admin_site.admin_view(self.invoice_view),
                name="orders_order_invoice",
            ),
            path(
                "<uuid:pk>/status/", self.admin_site.admin_view(self.status_view), name="orders_order_status"
            ),
            path(
                "<uuid:pk>/invoice.pdf",
                self.admin_site.admin_view(self.invoice_pdf_view),
                name="orders_order_invoice_pdf",
            ),
            path(
                "<uuid:pk>/email-invoice/",
                self.admin_site.admin_view(self.email_invoice_view),
                name="orders_order_email_invoice",
            ),
        ]
        return custom + super().get_urls()

    def _render_invoices(self, request, orders):
        orders = orders.select_related("user").prefetch_related("items", "charges")
        context = {
            **self.admin_site.each_context(request),
            "invoices": [invoice_context(order) for order in orders],
            "back_url": request.headers.get("referer") or reverse("admin:orders_order_changelist"),
        }
        if len(context["invoices"]) == 1:
            context["pdf_url"] = reverse(
                "admin:orders_order_invoice_pdf", args=[context["invoices"][0]["order"].pk]
            )
        return TemplateResponse(request, "orders/invoice.html", context)

    def invoice_view(self, request, pk):
        if not self.has_view_permission(request):
            raise Http404
        orders = Order.objects.filter(pk=pk)
        if not orders.exists():
            raise Http404
        return self._render_invoices(request, orders)

    # ---- status changes -----------------------------------------------------------------------------------

    def _run_status_action(self, action: str, order: Order, request, data) -> str:
        """Run one step through its service; returns a message for the staff member."""
        actor = request.user
        if action == "confirm":
            checkout.confirm_order(order.pk, actor=actor)
        elif action == "ship":
            checkout.mark_shipped(
                order.pk,
                actor=actor,
                courier_name=data.get("courier_name", ""),
                tracking_number=data.get("tracking_number", ""),
            )
        elif action == "deliver":
            checkout.mark_delivered(order.pk, actor=actor)
        elif action == "rto":
            checkout.mark_return_to_origin(order.pk, actor=actor)
        elif action == "cancel":
            checkout.cancel_order_by_staff(order.pk, actor=actor, note=data.get("note", ""))
        else:
            raise Http404
        return STATUS_ACTIONS[action][0]

    def _apply(self, request, orders, action: str, data=None) -> None:
        done, problems = 0, []
        for order in orders:
            if order.status not in STATUS_ACTIONS[action][1]:
                problems.append(
                    f"{order.order_number}: not possible while the order is {order.get_status_display()}"
                )
                continue
            try:
                self._run_status_action(action, order, request, data or {})
                done += 1
            except (InvalidTransition, ValidationError) as exc:
                problems.append(f"{order.order_number}: {getattr(exc, 'detail', exc)}")
        if done:
            label = STATUS_ACTIONS[action][0].lower()
            self.message_user(request, f"{label.capitalize()}: {done} order(s) updated.", messages.SUCCESS)
        for problem in problems:
            self.message_user(request, problem, messages.ERROR)

    def status_view(self, request, pk):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        order = Order.objects.filter(pk=pk).first()
        if order is None or not self.has_change_permission(request, order):
            raise Http404
        action = request.POST.get("action", "")
        if action not in STATUS_ACTIONS:
            raise Http404
        self._apply(request, [order], action, request.POST)
        return redirect("admin:orders_order_change", pk)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        order = Order.objects.filter(pk=object_id).first()
        extra_context = {**(extra_context or {})}
        if order is not None and self.has_change_permission(request, order):
            extra_context["status_actions"] = [
                {"key": key, "label": label, "style": style, "confirm": confirm}
                for key, (label, statuses, style, confirm) in STATUS_ACTIONS.items()
                if order.status in statuses
            ]
        return super().change_view(request, object_id, form_url, extra_context)

    @admin.action(description="Accept the selected pending orders")
    def accept_orders(self, request, queryset):
        self._apply(request, queryset.order_by("number"), "confirm")

    @admin.action(description="Mark the selected confirmed orders as shipped")
    def ship_orders(self, request, queryset):
        self._apply(request, queryset.order_by("number"), "ship")

    @admin.action(description="Mark the selected shipped orders as delivered")
    def deliver_orders(self, request, queryset):
        self._apply(request, queryset.order_by("number"), "deliver")

    def _email_invoices(self, request, orders) -> None:
        if not settings.NOTIFICATIONS_ENABLED:
            self.message_user(
                request,
                "Customer emails are turned off (NOTIFICATIONS_ENABLED=false), so no invoice was sent.",
                messages.WARNING,
            )
            return
        sent = failed = 0
        for order in orders:
            log = notifications.send_invoice(order.pk, resend=True)
            if log is not None and log.status == EmailLog.Status.SENT:
                sent += 1
            else:
                failed += 1
        if sent:
            self.message_user(request, f"Invoice emailed to {sent} customer(s).", messages.SUCCESS)
        if failed:
            self.message_user(
                request,
                f"{failed} invoice email(s) could not be sent; see the order's Emails section.",
                messages.ERROR,
            )

    def email_invoice_view(self, request, pk):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        if not self.has_change_permission(request) or not Order.objects.filter(pk=pk).exists():
            raise Http404
        self._email_invoices(request, Order.objects.filter(pk=pk))
        return redirect("admin:orders_order_change", pk)

    @admin.action(description="Email the invoice to the customers of the selected orders")
    def email_invoices(self, request, queryset):
        self._email_invoices(request, queryset)

    def invoice_pdf_view(self, request, pk):
        if not self.has_view_permission(request):
            raise Http404
        order = (
            Order.objects.select_related("user").prefetch_related("items", "charges").filter(pk=pk).first()
        )
        if order is None:
            raise Http404
        context = invoice_context(order)
        response = HttpResponse(render_invoice_pdf(context), content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="Invoice-{context["invoice_number"]}.pdf"'
        return response

    @admin.action(description="Print invoices for the selected orders")
    def print_invoices(self, request, queryset):
        return self._render_invoices(request, queryset.order_by("number"))

    @admin.display(description="Invoice")
    def invoice_link(self, obj):
        return format_html(
            '<a href="{}" target="_blank" rel="noopener">Print</a>',
            reverse("admin:orders_order_invoice", args=[obj.pk]),
        )

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields if f.name not in (*self.TRACKING_FIELDS, "id")]

    def save_model(self, request, obj, form, change):
        # Only the tracking fields are editable here, so save just those and leave the rest untouched.
        obj.modified_by = request.user
        obj.save(update_fields=[*self.TRACKING_FIELDS, "modified_by", "updated_at"])
        if set(self.TRACKING_FIELDS) & set(form.changed_data):
            # Partner / tracking number assigned, changed or corrected: tell the customer once per value.
            notifications.after_commit(notifications.notify_tracking_assigned, obj.pk)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CodTransitPolicy)
class CodTransitPolicyAdmin(AuditAdminMixin, admin.ModelAdmin):
    """Staff-editable COD fee rules. Orders keep the amount they were priced with, so edits are safe."""

    list_display = ("__str__", "scope", "match", "mode", "value", "is_active", "effective_from")
    list_filter = ("scope", "mode", "is_active")
    list_editable = ("is_active",)
    fieldsets = (
        (None, {"fields": ("scope", "match", "is_active", "effective_from")}),
        ("Fee", {"fields": ("mode", "value", "min_fee", "max_fee")}),
        ("Only for orders worth", {"fields": ("min_order_value", "max_order_value")}),
    )
