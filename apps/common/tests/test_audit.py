from types import SimpleNamespace

from apps.common.admin import AuditAdminMixin
from apps.common.models import AuditModel


def test_audit_model_fields_are_nullable_and_not_editable():
    for name in ("created_by", "modified_by"):
        field = AuditModel._meta.get_field(name)
        assert field.null and not field.editable
        assert field.remote_field.on_delete.__name__ == "SET_NULL"


class _Base:
    def get_readonly_fields(self, request, obj=None):
        return ()

    def save_model(self, request, obj, form, change):
        obj.saved = True


class _Admin(AuditAdminMixin, _Base):
    pass


def test_mixin_sets_creator_once_and_modifier_always():
    admin, creator, editor = _Admin(), object(), object()
    obj = SimpleNamespace(created_by_id=None, created_by=None, modified_by=None)

    admin.save_model(SimpleNamespace(user=creator), obj, None, change=False)
    assert obj.created_by is creator and obj.modified_by is creator and obj.saved

    obj.created_by_id = 1
    admin.save_model(SimpleNamespace(user=editor), obj, None, change=True)
    assert obj.created_by is creator and obj.modified_by is editor


def test_mixin_marks_audit_fields_readonly():
    assert "created_by" in _Admin().get_readonly_fields(None)


def test_base_models_use_uuid_primary_keys():
    import uuid

    from apps.accounts.models import AuthToken, OtpChallenge, User
    from apps.common.models import TimeStampedModel

    for model in (User, OtpChallenge, AuthToken, TimeStampedModel, AuditModel):
        pk = model._meta.pk
        assert pk.name == "id" and pk.get_internal_type() == "UUIDField"
        assert pk.default is uuid.uuid4
