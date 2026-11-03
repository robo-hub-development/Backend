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
    assert staff_client.get(reverse("admin:cart_cartitem_changelist")).status_code == 200
