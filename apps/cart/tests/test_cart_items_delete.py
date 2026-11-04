import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.cart.models import CartItem
from apps.catalog.tests.conftest import make_product

from .conftest import client_for


def fill(api, *products):
    res = api.put(
        "/api/v1/cart",
        {"items": [{"productId": str(p.pk), "quantity": 1} for p in products]},
        format="json",
    )
    return {item["productId"]: item["id"] for item in res.data["items"]}


def url(item_id):
    return f"/api/v1/cart/items/{item_id}"


@pytest.mark.django_db
class TestRemoveCartItem:
    def test_removes_the_line_and_returns_the_updated_cart(self, api):
        keep, drop = make_product(), make_product()
        ids = fill(api, keep, drop)

        res = api.delete(url(ids[str(drop.pk)]))

        assert res.status_code == 200
        assert [i["productId"] for i in res.data["items"]] == [str(keep.pk)]
        assert CartItem.objects.count() == 1
        assert [i["productId"] for i in api.get("/api/v1/cart").data["items"]] == [str(keep.pk)]

    def test_removing_twice_is_404(self, api):
        item_id = next(iter(fill(api, make_product()).values()))
        assert api.delete(url(item_id)).status_code == 200
        assert api.delete(url(item_id)).status_code == 404

    def test_unknown_id_is_404(self, api):
        assert api.delete(url(uuid.uuid4())).status_code == 404

    def test_another_users_line_is_404_and_untouched(self, api, other_member):
        theirs = next(iter(fill(client_for(other_member), make_product()).values()))
        assert api.delete(url(theirs)).status_code == 404
        assert CartItem.objects.filter(pk=theirs).exists()

    def test_ordered_history_cannot_be_removed(self, api):
        item_id = next(iter(fill(api, make_product()).values()))
        CartItem.objects.update(ordered_at=timezone.now() - timedelta(days=1))
        assert api.delete(url(item_id)).status_code == 404
        assert CartItem.objects.filter(pk=item_id).exists()

    def test_requires_authentication(self, api):
        item_id = next(iter(fill(api, make_product()).values()))
        assert APIClient().delete(url(item_id)).status_code == 401
        assert CartItem.objects.filter(pk=item_id).exists()

    def test_malformed_id_does_not_match_the_route(self, api):
        assert api.delete("/api/v1/cart/items/not-a-uuid").status_code == 404
