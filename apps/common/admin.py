from apps.common.richtext import clean_html
from apps.common.widgets import RichTextWidget


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


class RichTextAdminMixin:
    """Edit the listed text fields with the rich text editor and clean their HTML when saved.

    Put it before ModelAdmin in the bases: `rich_text_fields = ("description",)`."""

    rich_text_fields: tuple[str, ...] = ()

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name in self.rich_text_fields:
            kwargs["widget"] = RichTextWidget()
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        for name in self.rich_text_fields:
            setattr(obj, name, clean_html(getattr(obj, name)))
        super().save_model(request, obj, form, change)
