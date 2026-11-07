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


def fill_cart(api, *lines) -> str:
    """Put the lines into the caller's server cart through the API and return the cart id."""
    res = api.put(
        "/api/v1/cart",
        {"items": [{"productId": str(p.pk), "quantity": q} for p, q in lines]},
        format="json",
    )
    assert res.status_code == 200, res.data
    return res.data["id"]


def live_line_ids(cart_id) -> list[str]:
    lines = CartItem.objects.filter(cart_id=cart_id, ordered_at__isnull=True)
    return [str(pk) for pk in lines.values_list("pk", flat=True)]


def order_from(api, cart_id, *, cart_items=None, method="cod", address=None):
    """Order from `cart_id`; by default every live line of that cart is listed in `cartItems`."""
    body = {
        "cartId": str(cart_id),
        "cartItems": live_line_ids(cart_id) if cart_items is None else cart_items,
        "address": address or ADDRESS,
        "paymentMethod": method,
    }
    return api.post(URL, body, format="json")


def place(api, *lines, method="cod", address=None):
    """Fill the cart with `lines`, then order from it."""
    return order_from(api, fill_cart(api, *lines), method=method, address=address)


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

    def test_order_is_built_from_the_cart_and_consumes_its_lines(self, api, member):
        product = make_product(price=250)
        cart_id = fill_cart(api, (product, 1))
        line = CartItem.objects.get()

        res = order_from(api, cart_id)

        assert res.status_code == 201
        order = Order.objects.get()
        assert str(order.cart_id) == cart_id
        assert order.items.get().cart_item == line
        assert order.total == 250
        line.refresh_from_db()
        assert line.ordered_at is not None  # kept as history, not deleted
        assert api.get("/api/v1/cart").data["items"] == []

    def test_items_in_the_payload_are_ignored(self, api):
        in_cart, other = make_product(), make_product()
        cart_id = fill_cart(api, (in_cart, 1))
        body = {
            "cartId": cart_id,
            "cartItems": live_line_ids(cart_id),
            "items": [{"productId": str(other.pk), "quantity": 1}],
            "address": ADDRESS,
            "paymentMethod": "cod",
        }
        assert api.post(URL, body, format="json").status_code == 201
        assert Order.objects.get().items.get().product == in_cart

    def test_cart_id_is_required(self, api):
        fill_cart(api, (make_product(), 1))
        res = api.post(URL, {"address": ADDRESS, "paymentMethod": "cod"}, format="json")
        assert res.status_code == 400
        assert "cartId" in res.data["errors"]

    def test_cart_items_are_required_and_not_empty(self, api):
        cart_id = fill_cart(api, (make_product(), 1))
        body = {"cartId": cart_id, "address": ADDRESS, "paymentMethod": "cod"}
        assert "cartItems" in api.post(URL, body, format="json").data["errors"]
        assert order_from(api, cart_id, cart_items=[]).status_code == 400
        assert not Order.objects.exists()

    def test_double_submit_creates_one_order_and_takes_stock_once(self, api):
        product = make_product(stock_quantity=3)
        cart_id = fill_cart(api, (product, 1))
        ordered_ids = live_line_ids(cart_id)
        assert order_from(api, cart_id).status_code == 201
        again = order_from(api, cart_id, cart_items=ordered_ids)
        assert again.status_code == 400
        assert "cartItems" in again.data["errors"]
        assert Order.objects.count() == 1
        product.refresh_from_db()
        assert product.stock_quantity == 2

    def test_cart_of_another_user_is_rejected_without_side_effects(self, api, other_member):
        product = make_product(stock_quantity=4)
        foreign_id = fill_cart(client_for(other_member), (product, 1))
        res = order_from(api, foreign_id)
        assert res.status_code == 400
        assert "cartId" in res.data["errors"]
        assert not Order.objects.exists()
        product.refresh_from_db()
        assert product.stock_quantity == 4
        assert CartItem.objects.get().ordered_at is None

    def test_unknown_or_malformed_cart_id(self, api):
        import uuid

        ids = live_line_ids(fill_cart(api, (make_product(), 1)))
        assert order_from(api, uuid.uuid4(), cart_items=ids).status_code == 400
        assert order_from(api, "not-a-uuid", cart_items=ids).status_code == 400
        assert not Order.objects.exists()

    def test_only_the_listed_cart_items_are_ordered_and_mapped(self, api):
        wanted, left = make_product(price=300), make_product(price=40)
        cart_id = fill_cart(api, (wanted, 1), (left, 2))
        wanted_line = CartItem.objects.get(product=wanted)

        res = order_from(api, cart_id, cart_items=[str(wanted_line.pk)])

        assert res.status_code == 201
        item = OrderItem.objects.get()
        assert item.cart_item == wanted_line  # the listed id is mapped straight onto the order item
        assert Order.objects.get().total == 300
        wanted_line.refresh_from_db()
        assert wanted_line.ordered_at is not None
        assert CartItem.objects.get(product=left).ordered_at is None
        assert [i["productId"] for i in api.get("/api/v1/cart").data["items"]] == [str(left.pk)]

    def test_cart_item_of_another_users_cart_is_rejected(self, api, other_member):
        mine = fill_cart(api, (make_product(), 1))
        theirs = fill_cart(client_for(other_member), (make_product(stock_quantity=4), 1))
        foreign_line = live_line_ids(theirs)
        res = order_from(api, mine, cart_items=foreign_line)
        assert res.status_code == 400
        assert "cartItems" in res.data["errors"]
        assert not Order.objects.exists()
        assert CartItem.objects.filter(ordered_at__isnull=False).count() == 0

    def test_unknown_duplicate_and_already_ordered_cart_items_are_rejected(self, api):
        import uuid

        product = make_product(stock_quantity=5)
        cart_id = fill_cart(api, (product, 1))
        (line_id,) = live_line_ids(cart_id)
        assert order_from(api, cart_id, cart_items=[str(uuid.uuid4())]).status_code == 400
        assert order_from(api, cart_id, cart_items=[line_id, line_id]).status_code == 400
        assert order_from(api, cart_id, cart_items=[line_id, str(uuid.uuid4())]).status_code == 400
        assert not Order.objects.exists()
        product.refresh_from_db()
        assert product.stock_quantity == 5
        assert order_from(api, cart_id, cart_items=[line_id]).status_code == 201
        assert order_from(api, cart_id, cart_items=[line_id]).status_code == 400  # now already ordered

    def test_product_can_be_added_again_after_it_was_ordered(self, api):
        product = make_product(stock_quantity=5)
        place(api, (product, 1))
        assert api.get("/api/v1/cart").data["items"] == []
        assert fill_cart(api, (product, 3))
        assert api.get("/api/v1/cart").data["items"][0]["quantity"] == 3

    def test_deleting_a_cart_line_keeps_the_order_item(self, api):
        place(api, (make_product(), 1))
        CartItem.objects.get().delete()
        assert OrderItem.objects.get().cart_item is None

    def test_cod_rejects_quantity_above_one(self, api):
        product = make_product(stock_quantity=9)
        res = place(api, (product, 2))
        assert res.status_code == 400
        assert res.data["message"] == "Cash on delivery is available only for a single item."
        assert not Order.objects.exists()
        product.refresh_from_db()
        assert product.stock_quantity == 9

    def test_cod_rejects_multiple_lines_and_leaves_the_cart_alone(self, api):
        res = place(api, (make_product(), 1), (make_product(), 1))
        assert res.status_code == 400
        assert not Order.objects.exists()
        assert CartItem.objects.filter(ordered_at__isnull=True).count() == 2

    def test_out_of_stock_rejected_without_side_effects(self, api):
        product = make_product(stock_quantity=0)
        res = place(api, (product, 1))
        assert res.status_code == 400
        assert "out of stock" in res.data["message"]
        assert not Order.objects.exists()

    def test_product_deactivated_after_being_added_cannot_be_ordered(self, api):
        product = make_product(stock_quantity=2)
        cart_id = fill_cart(api, (product, 1))
        product.is_active = False
        product.save()
        res = order_from(api, cart_id)
        assert res.status_code == 400
        assert not Order.objects.exists()
        product.refresh_from_db()
        assert product.stock_quantity == 2

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
        assert Order.TRANSITIONS[S.AWAITING_PAYMENT] == {S.PENDING, S.CONFIRMED, S.CANCELLED, S.EXPIRED}
        assert Order.TRANSITIONS[S.PENDING] == {S.CONFIRMED, S.CANCELLED}
        assert Order.TRANSITIONS[S.CONFIRMED] == {S.SHIPPED, S.CANCELLED}
        assert Order.TRANSITIONS[S.SHIPPED] == {S.DELIVERED, S.RTO}
        assert Order.TRANSITIONS[S.EXPIRED] == {S.PENDING, S.CONFIRMED}  # only a late payment revives it
        for terminal in (S.DELIVERED, S.CANCELLED, S.RTO):
            assert not Order.TRANSITIONS[terminal]
        assert set(Order.TRANSITIONS) == set(S)  # no status is missing from the map


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


@pytest.mark.django_db
def test_one_order_can_hold_many_cart_items(api, member):
    """The schema is multi-line; only the COD rule limits COD orders (online orders arrive in Phase 5)."""
    first, second = make_product(price=100), make_product(price=50)
    cart_id = fill_cart(api, (first, 2), (second, 3))
    lines = {line.product_id: line for line in CartItem.objects.all()}
    order = Order.objects.create(
        user=member,
        cart_id=cart_id,
        number=999001,
        payment_method=Order.PaymentMethod.ONLINE,
        subtotal=Decimal(350),
        total=Decimal(350),
        ship_full_name="A",
        ship_mobile="9876543210",
        ship_line1="x",
        ship_city="c",
        ship_state="s",
        ship_pincode="560001",
    )
    for product in (first, second):
        line = lines[product.pk]
        OrderItem.objects.create(
            order=order,
            product=product,
            cart_item=line,
            product_name=product.name,
            product_slug=product.slug,
            unit_price=product.price,
            quantity=line.quantity,
        )

    assert order.items.count() == 2
    assert {item.cart_item_id for item in order.items.all()} == {line.pk for line in lines.values()}
    data = api.get(f"{URL}/{order.pk}").data
    assert [i["quantity"] for i in data["items"]] == [2, 3]
    assert data["total"] == 350
