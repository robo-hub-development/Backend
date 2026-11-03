import uuid
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.cart.models import CartItem
from apps.catalog.tests.conftest import make_product

from .conftest import client_for

URL = "/api/v1/cart"


def put(api, *lines):
    return api.put(URL, {"items": [{"productId": str(p.pk), "quantity": q} for p, q in lines]}, format="json")


@pytest.mark.django_db
class TestCartApi:
    def test_requires_authentication(self):
        anon = APIClient()
        assert anon.get(URL).status_code == 401
        assert anon.put(URL, {"items": []}, format="json").status_code == 401

    def test_empty_cart(self, api):
        res = api.get(URL)
        assert res.status_code == 200
        assert res.data == {"items": []}

    def test_put_returns_priced_lines_from_the_database(self, api):
        product = make_product("ESP32 DevKit", price=599)
        res = put(api, (product, 2))
        assert res.status_code == 200
        (line,) = res.data["items"]
        assert line["productId"] == str(product.pk)
        assert line["slug"] == product.slug
        assert line["name"] == "ESP32 DevKit"
        assert line["price"] == 599
        assert line["quantity"] == 2
        assert line["image"].startswith("http")
        assert api.get(URL).data == res.data

    def test_client_cannot_set_the_price(self, api):
        product = make_product(price=599)
        payload = {"items": [{"productId": str(product.pk), "quantity": 1, "price": 1}]}
        assert api.put(URL, payload, format="json").data["items"][0]["price"] == 599

    def test_put_replaces_the_whole_cart(self, api):
        a, b = make_product(), make_product()
        put(api, (a, 1))
        res = put(api, (b, 3))
        assert [i["productId"] for i in res.data["items"]] == [str(b.pk)]
        assert CartItem.objects.count() == 1

    def test_empty_items_clears_the_cart(self, api):
        put(api, (make_product(), 1))
        assert put(api).data == {"items": []}
        assert CartItem.objects.count() == 0

    @pytest.mark.parametrize("quantity", [0, 11, -1])
    def test_quantity_must_be_1_to_10(self, api, quantity):
        res = put(api, (make_product(), quantity))
        assert res.status_code == 400
        assert CartItem.objects.count() == 0

    def test_ten_is_allowed(self, api):
        assert put(api, (make_product(), 10)).status_code == 200

    def test_duplicate_product_rejected_and_old_cart_kept(self, api):
        keep = make_product()
        put(api, (keep, 1))
        dup = make_product()
        res = put(api, (dup, 1), (dup, 2))
        assert res.status_code == 400
        assert [i["productId"] for i in api.get(URL).data["items"]] == [str(keep.pk)]

    def test_unknown_inactive_product_rejected(self, api):
        inactive = make_product(is_active=False)
        assert put(api, (inactive, 1)).status_code == 400
        res = api.put(URL, {"items": [{"productId": str(uuid.uuid4()), "quantity": 1}]}, format="json")
        assert res.status_code == 400

    def test_malformed_body(self, api):
        assert (
            api.put(URL, {"items": [{"productId": "p_101", "quantity": 1}]}, format="json").status_code == 400
        )
        assert api.put(URL, {}, format="json").status_code == 400

    def test_product_deactivated_later_disappears_from_the_cart(self, api):
        product = make_product()
        put(api, (product, 1))
        product.is_active = False
        product.save()
        assert api.get(URL).data == {"items": []}

    def test_price_changes_are_reflected(self, api):
        product = make_product(price=100)
        put(api, (product, 1))
        product.price = Decimal("120")
        product.save()
        assert api.get(URL).data["items"][0]["price"] == 120

    def test_carts_are_private(self, api, other_member):
        put(api, (make_product(), 1))
        assert client_for(other_member).get(URL).data == {"items": []}

    def test_constraints_hold_at_the_database_level(self, member):
        from django.db import IntegrityError, transaction

        product = make_product()
        with pytest.raises(IntegrityError), transaction.atomic():
            CartItem.objects.create(user=member, product=product, quantity=11)
        CartItem.objects.create(user=member, product=product, quantity=1)
        with pytest.raises(IntegrityError), transaction.atomic():
            CartItem.objects.create(user=member, product=product, quantity=2)
