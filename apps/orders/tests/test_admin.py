import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.tests.conftest import make_product
from apps.orders.models import Order

from .test_orders_api import place


@pytest.fixture
def staff_client(client, db):
    admin = User.objects.create_superuser(
        email="root@example.com", name="Root", mobile="9000000001", password="s3cret-pass-1"
    )
    client.force_login(admin)
    return client


@pytest.mark.django_db
def test_orders_are_visible_but_read_only_in_admin(staff_client, api):
    place(api, (make_product(), 1))
    order = Order.objects.get()

    assert staff_client.get(reverse("admin:orders_order_changelist")).status_code == 200
    detail = staff_client.get(reverse("admin:orders_order_change", args=[order.pk]))
    assert detail.status_code == 200
    assert order.order_number.encode() in detail.content
    assert staff_client.get(reverse("admin:orders_order_add")).status_code == 403
    assert (
        staff_client.post(reverse("admin:orders_order_delete", args=[order.pk]), {"post": "yes"}).status_code
        == 403
    )
    assert staff_client.get(reverse("admin:cart_cart_changelist")).status_code == 200


@pytest.mark.django_db
def test_every_order_field_appears_once_in_one_ungrouped_grid(staff_client, api):
    from django.contrib import admin

    place(api, (make_product(), 1))
    order = Order.objects.get()
    model_admin = admin.site._registry[Order]

    assert len(model_admin.fieldsets) == 1 and model_admin.fieldsets[0][0] is None  # one section, no heading
    (row,) = model_admin.fieldsets[0][1]["fields"]  # all fields in a single wrapping row
    expected = {f.name for f in Order._meta.fields if f.name != "id"}
    assert sorted(row) == sorted(expected)  # nothing missing, nothing twice
    assert row[:2] == ("courier_name", "tracking_number")  # the editable fields come first

    page = staff_client.get(reverse("admin:orders_order_change", args=[order.pk]))
    assert page.status_code == 200
    html = page.content.decode()
    assert html.count("fieldBox") >= len(expected)  # one box per field, inside the one grid
    assert html.count('class="form-multiline"') + html.count("form-multiline") >= 1
    for heading in ("Delivery partner", "Amounts", "Delivery address", "Record"):
        assert f">{heading}</h2>" not in html  # no sections
    assert 'name="status"' not in html  # still read-only: the layout does not make it editable
