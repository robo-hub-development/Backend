from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm

from .models import User

# Fields that grant power; only superusers may edit them (full role management lands in Phase 7).
PRIVILEGE_FIELDS = ("is_superuser", "groups", "user_permissions")


class UserCreationForm(AdminUserCreationForm):
    class Meta:
        model = User
        fields = ("email", "name", "mobile")


class UserChangeForm(UserChangeForm):  # noqa: F811 - email-based variant of Django's form
    class Meta:
        model = User
        fields = "__all__"


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    add_form = UserCreationForm
    form = UserChangeForm
    ordering = ("email",)
    list_display = ("email", "name", "mobile", "is_staff", "is_active", "created_at")
    list_filter = ("is_staff", "is_active", "is_superuser")
    search_fields = ("email", "name", "mobile")
    readonly_fields = ("last_login", "created_at", "updated_at")
    filter_horizontal = ("groups", "user_permissions")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("name", "mobile")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Dates", {"fields": ("last_login", "created_at", "updated_at")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "description": "Customers sign in with an emailed code, so choose 'Disable password-based "
                "authentication' for them. Staff need a password to use the admin.",
                "fields": ("email", "name", "mobile", "usable_password", "password1", "password2"),
            },
        ),
    )

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        if request.user.is_superuser:
            return fields
        return (*fields, *PRIVILEGE_FIELDS)
