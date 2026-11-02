import pytest

from apps.accounts.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture
def superuser(db):
    return User.objects.create_superuser(
        email="root@example.com", name="Root", password="pass-12345!", mobile="9000000013"
    )


def test_user_changelist_lists_users(client, superuser):
    User.objects.create_user(email="cust@example.com", name="Cust", mobile="9876543210")
    client.force_login(superuser)
    response = client.get("/admin/accounts/user/")
    assert response.status_code == 200
    assert b"cust@example.com" in response.content
    assert b"Users" in response.content


def test_user_change_page_renders_and_hides_secrets(client, superuser):
    client.force_login(superuser)
    response = client.get(f"/admin/accounts/user/{superuser.pk}/change/")
    assert response.status_code == 200
    assert b"pass-12345" not in response.content


def test_superuser_can_create_staff_with_password(client, superuser):
    client.force_login(superuser)
    assert client.get("/admin/accounts/user/add/").status_code == 200
    response = client.post(
        "/admin/accounts/user/add/",
        {
            "email": "Staff@Example.com",
            "name": "Staff",
            "mobile": "9811111111",
            "usable_password": "true",
            "password1": "Str0ng-pass-987",
            "password2": "Str0ng-pass-987",
        },
    )
    assert response.status_code == 302
    created = User.objects.get(email="staff@example.com")
    assert created.check_password("Str0ng-pass-987")


def test_superuser_can_create_otp_only_customer(client, superuser):
    client.force_login(superuser)
    response = client.post(
        "/admin/accounts/user/add/",
        {"email": "c2@example.com", "name": "C2", "mobile": "9876543210", "usable_password": "false"},
    )
    assert response.status_code == 302
    assert not User.objects.get(email="c2@example.com").has_usable_password()


def test_non_superuser_cannot_escalate_privileges(client):
    from django.contrib.auth.models import Permission

    staff = User.objects.create_user(
        email="mgr@example.com", name="Mgr", password="pass-12345!", is_staff=True, mobile="9000000014"
    )
    staff.user_permissions.add(
        *Permission.objects.filter(content_type__app_label="accounts", content_type__model="user")
    )
    victim = User.objects.create_user(email="v@example.com", name="V", mobile="9000000015")
    client.force_login(staff)
    page = client.get(f"/admin/accounts/user/{victim.pk}/change/")
    assert page.status_code == 200
    assert b'name="is_superuser"' not in page.content and b'name="user_permissions"' not in page.content
    client.post(
        f"/admin/accounts/user/{victim.pk}/change/",
        {
            "email": victim.email,
            "name": "V",
            "mobile": "",
            "is_active": "on",
            "is_staff": "on",
            "is_superuser": "on",
        },
    )
    victim.refresh_from_db()
    assert not victim.is_superuser


def test_admin_rejects_duplicate_mobile(client, superuser):
    User.objects.create_user(email="first@example.com", name="First", mobile="9876543210")
    client.force_login(superuser)
    response = client.post(
        "/admin/accounts/user/add/",
        {"email": "second@example.com", "name": "S", "mobile": "9876543210", "usable_password": "false"},
    )
    assert response.status_code == 200  # form re-rendered with an error, nothing created
    assert b"mobile number already exists" in response.content
    assert not User.objects.filter(email="second@example.com").exists()


def test_admin_requires_mobile(client, superuser):
    client.force_login(superuser)
    response = client.post(
        "/admin/accounts/user/add/",
        {"email": "nomobile@example.com", "name": "N", "mobile": "", "usable_password": "false"},
    )
    assert response.status_code == 200
    assert b"This field is required" in response.content
    assert not User.objects.filter(email="nomobile@example.com").exists()
