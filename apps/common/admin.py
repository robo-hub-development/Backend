class AuditAdminMixin:
    """Stamp created_by / modified_by from the logged-in staff user. Put first in the bases."""

    audit_readonly_fields = ("created_at", "created_by", "updated_at", "modified_by")

    def get_readonly_fields(self, request, obj=None):
        return (*super().get_readonly_fields(request, obj), *self.audit_readonly_fields)

    def save_model(self, request, obj, form, change):
        if not change and obj.created_by_id is None:
            obj.created_by = request.user
        obj.modified_by = request.user
        super().save_model(request, obj, form, change)
