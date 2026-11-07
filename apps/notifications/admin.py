from django.contrib import admin

from .models import EmailLog


@admin.register(EmailLog)
class EmailLogAdmin(admin.ModelAdmin):
    """Read-only record of customer emails. Failed ones are retried by `manage.py retry_failed_emails`."""

    list_display = ("created_at", "kind", "order", "recipient", "status", "attempts", "sent_at", "error")
    list_filter = ("kind", "status")
    search_fields = ("recipient", "order__user__email", "subject")
    list_select_related = ("order",)
    date_hierarchy = "created_at"

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields if f.name != "id"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
