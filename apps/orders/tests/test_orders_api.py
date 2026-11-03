import threading
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.utils import timezone
from rest_framework.test import APIClient

from apps.cart.models import CartItem
from apps.catalog.tests.conftest import make_product
from apps.orders.models import Order, OrderItem

from .conftest import client_for

URL = "/api/v1/orders"
ADDRESS = {
    "fullName": "Aarav Mehta",
    "mobile": "9876543210",
    "line1": "12 MG Road",
    "line2": "Near the metro",
    "city": "Bengaluru",
    "state": "Karnataka",
    "pincode": "560001",
}


def place(api, *lines, method="cod", address=None):
    body = {
        "items": [{"productId": str(p.pk), "quantity": q} for p, q in lines],
        "address": address or ADDRESS,
        "paymentMethod": method,
    }
    return api.post(URL, body, format="json")


@pytest.mark.django_db
class TestPlaceCodOrder:
    def test_requires_authentication(self):
        assert APIClient().post(URL, {}, format="json").status_code == 401

    def test_places_order_snapshots_and_decrements_stock(self, api, member):
        product = make_product("ESP32 DevKit", price=599, stock_quantity=5)
        res = place(api, (product, 1))
        assert res.status_code == 201
        order = res.data["order"]
        assert order["orderNumber"] == f"SRH-{Order.objects.get().number}"
        assert order["status"] == "pending"
        assert order["paymentMethod"] == "cod"
        assert order["paymentStatus"] == "pending"
        assert order["total"] == 599
        assert order["canCancel"] is True
        assert order["items"][0]["productId"] == str(product.pk)
        assert order["items"][0]["price"] == 599
        assert order["items"][0]["image"].startswith("http")
        product.refresh_from_db()
        assert product.stock_quantity == 4

        stored = Order.objects.get()
        assert stored.user == member
        assert (stored.ship_city, stored.ship_pincode, stored.ship_line2) == (
            "Bengaluru",
            "560001",
            "Near the metro",
        )
        assert stored.created_by == member

    def test_order_survives_later_catalogue_edits(self, api):
        product = make_product("Old Name", price=100)
        order_id = place(api, (product, 1)).data["order"]["id"]
        product.name, product.price = "New Name", Decimal(999)
        product.save()
        data = api.get(f"{URL}/{order_id}").data
        assert data["items"][0]["name"] == "Old Name"
        assert data["items"][0]["price"] == 100
        assert data["total"] == 100

    def test_order_numbers_are_sequential_and_start_at_1001_or_later(self, api):
        # Postgres sequences are not rolled back between tests, so only the relative order is exact.
        numbers = [int(place(api, (make_product(), 1)).data["order"]["orderNumber"][4:]) for _ in range(2)]
        assert numbers[0] >= 1001
        assert numbers[1] == numbers[0] + 1

    def test_placing_removes_only_the_ordered_product_from_the_cart(self, api, member):
        ordered, other = make_product(), make_product()
        CartItem.objects.create(user=member, product=ordered, quantity=1)
        CartItem.objects.create(user=member, product=other, quantity=2)
        place(api, (ordered, 1))
        assert list(CartItem.objects.values_list("product_id", flat=True)) == [other.pk]

    def test_cod_rejects_quantity_above_one(self, api):
        product = make_product(stock_quantity=9)
        res = place(api, (product, 2))
        assert res.status_code == 400
        assert res.data["message"] == "Cash on delivery is available only for a single item."
        assert not Order.objects.exists()
        product.refresh_from_db()
        assert product.stock_quantity == 9

    def test_cod_rejects_multiple_lines(self, api):
        res = place(api, (make_product(), 1), (make_product(), 1))
        assert res.status_code == 400
        assert not Order.objects.exists()

    def test_online_is_not_available_yet(self, api):
        product = make_product(stock_quantity=3)
        res = place(api, (product, 1), method="online")
        assert res.status_code == 400
        assert "paymentMethod" in res.data["errors"]
        assert not Order.objects.exists()

    def test_out_of_stock_rejected_without_side_effects(self, api):
        product = make_product(stock_quantity=0)
        res = place(api, (product, 1))
        assert res.status_code == 400
        assert "out of stock" in res.data["message"]
        assert not Order.objects.exists()

    def test_inactive_and_unknown_products_rejected(self, api):
        res = place(api, (make_product(is_active=False), 1))
        assert res.status_code == 400
        assert not Order.objects.exists()

    def test_rolls_back_stock_if_a_later_step_fails(self, api, monkeypatch):
        product = make_product(stock_quantity=3)
        monkeypatch.setattr(
            OrderItem.objects, "bulk_create", lambda *a, **k: (_ for _ in ()).throw(RuntimeError)
        )
        assert place(api, (product, 1)).status_code == 500  # unexpected failure -> generic 500, nothing kept
        product.refresh_from_db()
        assert product.stock_quantity == 3
        assert not Order.objects.exists()

    @pytest.mark.parametrize(
        "change",
        [
            {"mobile": "abc"},
            {"pincode": "56001"},
            {"pincode": "56000a"},
            {"fullName": ""},
            {"line1": ""},
            {"city": ""},
            {"state": ""},
        ],
    )
    def test_address_validation(self, api, change):
        res = place(api, (make_product(), 1), address={**ADDRESS, **change})
        assert res.status_code == 400
        assert "address" in res.data["errors"]

    def test_line2_is_optional(self, api):
        address = {k: v for k, v in ADDRESS.items() if k != "line2"}
        assert place(api, (make_product(), 1), address=address).status_code == 201

    def test_empty_items_and_bad_method(self, api):
        assert (
            api.post(
                URL, {"items": [], "address": ADDRESS, "paymentMethod": "cod"}, format="json"
            ).status_code
            == 400
        )
        res = place(api, (make_product(), 1), method="card")
        assert res.status_code == 400


@pytest.mark.django_db
class TestReadOrders:
    def test_history_is_paginated_newest_first_and_private(self, api, other_member):
        first = place(api, (make_product(), 1)).data["order"]["id"]
        second = place(api, (make_product(), 1)).data["order"]["id"]
        place(client_for(other_member), (make_product(), 1))
        res = api.get(URL)
        assert res.status_code == 200
        assert [o["id"] for o in res.data["items"]] == [second, first]
        assert res.data["total"] == 2
        assert {"page", "pageSize", "totalPages"} <= set(res.data)

    def test_detail_of_another_users_order_is_404(self, api, other_member):
        foreign = place(client_for(other_member), (make_product(), 1)).data["order"]["id"]
        assert api.get(f"{URL}/{foreign}").status_code == 404

    def test_unknown_order_is_404_and_listing_needs_auth(self, api):
        assert api.get(f"{URL}/3f2b9d6e-0000-4000-8000-000000000000").status_code == 404
        assert APIClient().get(URL).status_code == 401


@pytest.mark.django_db
class TestCancel:
    def test_cancel_restores_stock_and_flips_flags(self, api):
        product = make_product(stock_quantity=2)
        order_id = place(api, (product, 1)).data["order"]["id"]
        res = api.post(f"{URL}/{order_id}/cancel")
        assert res.status_code == 200
        assert res.data["status"] == "cancelled"
        assert res.data["canCancel"] is False
        product.refresh_from_db()
        assert product.stock_quantity == 2
        assert Order.objects.get().cancelled_at is not None

    def test_cancelling_twice_does_not_restore_stock_twice(self, api):
        product = make_product(stock_quantity=2)
        order_id = place(api, (product, 1)).data["order"]["id"]
        api.post(f"{URL}/{order_id}/cancel")
        again = api.post(f"{URL}/{order_id}/cancel")
        assert again.status_code == 400
        assert again.data["message"] == "This order can no longer be cancelled."
        product.refresh_from_db()
        assert product.stock_quantity == 2

    @pytest.mark.parametrize("status", ["shipped", "delivered"])
    def test_cannot_cancel_after_dispatch(self, api, status):
        order_id = place(api, (make_product(), 1)).data["order"]["id"]
        Order.objects.update(status=status)
        assert api.get(f"{URL}/{order_id}").data["canCancel"] is False
        assert api.post(f"{URL}/{order_id}/cancel").status_code == 400

    def test_confirmed_orders_can_still_be_cancelled(self, api):
        order_id = place(api, (make_product(), 1)).data["order"]["id"]
        Order.objects.update(status="confirmed")
        assert api.post(f"{URL}/{order_id}/cancel").status_code == 200

    def test_cancellation_window(self, api, settings):
        settings.ORDER_CANCEL_WINDOW_HOURS = 24
        order_id = place(api, (make_product(), 1)).data["order"]["id"]
        Order.objects.update(created_at=timezone.now() - timedelta(hours=25))
        assert api.get(f"{URL}/{order_id}").data["canCancel"] is False
        res = api.post(f"{URL}/{order_id}/cancel")
        assert res.status_code == 400
        assert Order.objects.get().status == "pending"

    def test_other_users_order_cannot_be_cancelled(self, api, other_member):
        product = make_product(stock_quantity=2)
        foreign = place(client_for(other_member), (product, 1)).data["order"]["id"]
        assert api.post(f"{URL}/{foreign}/cancel").status_code == 404
        assert Order.objects.get().status == "pending"
        product.refresh_from_db()
        assert product.stock_quantity == 1

    def test_cancel_requires_auth(self, api):
        order_id = place(api, (make_product(), 1)).data["order"]["id"]
        assert APIClient().post(f"{URL}/{order_id}/cancel").status_code == 401

    def test_transition_map(self):
        S = Order.Status
        assert Order.TRANSITIONS[S.PENDING] == {S.CONFIRMED, S.CANCELLED}
        assert Order.TRANSITIONS[S.CONFIRMED] == {S.SHIPPED, S.CANCELLED}
        assert Order.TRANSITIONS[S.SHIPPED] == {S.DELIVERED}
        assert not Order.TRANSITIONS[S.DELIVERED] and not Order.TRANSITIONS[S.CANCELLED]


@pytest.mark.django_db(transaction=True)
def test_concurrent_orders_for_the_last_unit_sell_it_only_once(member, other_member):
    product = make_product(stock_quantity=1)
    barrier = threading.Barrier(2)
    statuses = []

    def buy(user):
        try:
            client = client_for(user)
            barrier.wait()
            statuses.append(place(client, (product, 1)).status_code)
        finally:
            connection.close()  # each thread owns its own DB connection

    threads = [threading.Thread(target=buy, args=(u,)) for u in (member, other_member)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(statuses) == [201, 400]
    product.refresh_from_db()
    assert product.stock_quantity == 0
    assert Order.objects.count() == 1
