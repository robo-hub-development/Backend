import pytest
from django.db import IntegrityError, transaction
from rest_framework.test import APIClient

from apps.accounts.address_services import MAX_ADDRESSES
from apps.accounts.models import Address, User

from .conftest import login_token

URL = "/api/v1/addresses"

PAYLOAD = {
    "fullName": "Aarav Mehta",
    "mobile": "9876543210",
    "line1": "12 MG Road",
    "line2": "Near the metro",
    "city": "Bengaluru",
    "state": "Karnataka",
    "pincode": "560001",
}


@pytest.fixture
def client(api, user):
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {login_token(api, user.email)}")
    return api


def add(client, **overrides):
    return client.post(URL, {**PAYLOAD, **overrides}, format="json")


@pytest.fixture
def other_client(db):
    other = User.objects.create_user(email="other@example.com", name="Other", mobile="9123456780")
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {login_token(c, other.email)}")
    return c


class TestCreate:
    def test_first_address_is_default(self, client):
        response = add(client)
        assert response.status_code == 201
        assert response.data["isDefault"] is True

    def test_second_is_not_default_unless_asked(self, client):
        add(client)
        second = add(client, line1="Second").data
        assert second["isDefault"] is False
        third = add(client, line1="Third", isDefault=True).data
        assert third["isDefault"] is True
        defaults = [a for a in client.get(URL).data if a["isDefault"]]
        assert [a["line1"] for a in defaults] == ["Third"]

    def test_validation(self, client):
        response = add(client, pincode="12", mobile="abc", city="")
        assert response.status_code == 400
        assert {"pincode", "mobile", "city"} <= set(response.data["errors"])

    def test_limit(self, client):
        for i in range(MAX_ADDRESSES):
            assert add(client, line1=f"Line {i}").status_code == 201
        assert add(client, line1="One too many").status_code == 400

    def test_requires_login(self, db):
        assert APIClient().post(URL, PAYLOAD, format="json").status_code == 401
        assert APIClient().get(URL).status_code == 401


class TestListAndOwnership:
    def test_lists_only_own_default_first(self, client, other_client):
        add(client, line1="First")
        add(client, line1="Second", isDefault=True)
        add(other_client, line1="Not yours")
        lines = [a["line1"] for a in client.get(URL).data]
        assert lines == ["Second", "First"]

    def test_foreign_address_is_not_found(self, client, other_client):
        foreign_id = add(other_client).data["id"]
        assert client.get(f"{URL}/{foreign_id}").status_code == 404
        assert client.patch(f"{URL}/{foreign_id}", {"city": "X"}, format="json").status_code == 404
        assert client.delete(f"{URL}/{foreign_id}").status_code == 404
        assert Address.objects.filter(pk=foreign_id).exists()


class TestUpdate:
    def test_partial_update_keeps_other_fields(self, client):
        address_id = add(client).data["id"]
        response = client.patch(f"{URL}/{address_id}", {"city": "Mysuru"}, format="json")
        assert response.status_code == 200
        assert response.data["city"] == "Mysuru"
        assert response.data["line2"] == "Near the metro"
        assert response.data["pincode"] == "560001"

    def test_make_default_moves_the_flag(self, client):
        first = add(client).data["id"]
        second = add(client, line1="Second").data["id"]
        client.patch(f"{URL}/{second}", {"isDefault": True}, format="json")
        flags = {a["id"]: a["isDefault"] for a in client.get(URL).data}
        assert flags == {first: False, second: True}

    def test_default_cannot_be_switched_off_directly(self, client):
        address_id = add(client).data["id"]
        response = client.patch(f"{URL}/{address_id}", {"isDefault": False}, format="json")
        assert response.data["isDefault"] is True

    def test_invalid_update(self, client):
        address_id = add(client).data["id"]
        assert client.patch(f"{URL}/{address_id}", {"pincode": "abc"}, format="json").status_code == 400


class TestDelete:
    def test_deleting_default_promotes_the_latest_remaining(self, client):
        first = add(client, line1="First").data["id"]
        second = add(client, line1="Second").data["id"]
        add(client, line1="Third")
        assert client.delete(f"{URL}/{first}").status_code == 204
        flags = {a["line1"]: a["isDefault"] for a in client.get(URL).data}
        assert flags == {"Second": False, "Third": True}
        assert second  # still present

    def test_deleting_last_address(self, client):
        address_id = add(client).data["id"]
        assert client.delete(f"{URL}/{address_id}").status_code == 204
        assert client.get(URL).data == []


def test_database_allows_one_default_per_user(user):
    base = {
        "user": user,
        "full_name": "A",
        "mobile": "9876543210",
        "line1": "x",
        "city": "c",
        "state": "s",
        "pincode": "560001",
    }
    Address.objects.create(is_default=True, **base)
    with pytest.raises(IntegrityError), transaction.atomic():
        Address.objects.create(is_default=True, **base)
    Address.objects.create(is_default=False, **base)
