import pytest
from rest_framework import serializers
from rest_framework.exceptions import NotAuthenticated
from rest_framework.generics import ListAPIView
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.common.pagination import StandardPagination


class _Ser(serializers.Serializer):
    email = serializers.EmailField()


class _Validate(APIView):
    def post(self, request):
        s = _Ser(data=request.data)
        s.is_valid(raise_exception=True)


class _Unauth(APIView):
    def get(self, request):
        raise NotAuthenticated()


class _Boom(APIView):
    def get(self, request):
        raise RuntimeError("secret internal detail")


class _List(ListAPIView):
    serializer_class = _Ser
    pagination_class = StandardPagination

    def get_queryset(self):
        return [{"email": f"u{i}@x.com"} for i in range(30)]


factory = APIRequestFactory()


def test_validation_error_shape():
    response = _Validate.as_view()(factory.post("/x", {"email": "bad"}, format="json"))
    assert response.status_code == 400
    assert response.data["errors"]["email"]
    assert "message" in response.data


def test_not_authenticated_has_message_only():
    response = _Unauth.as_view()(factory.get("/x"))
    assert response.status_code in (401, 403)
    assert set(response.data) == {"message"}


def test_unhandled_error_hides_internals():
    response = _Boom.as_view()(factory.get("/x"))
    assert response.status_code == 500
    assert "secret" not in str(response.data)


def test_pagination_envelope():
    response = _List.as_view()(factory.get("/x", {"page": 2, "pageSize": 10}))
    assert response.data["page"] == 2
    assert response.data["pageSize"] == 10
    assert response.data["total"] == 30
    assert response.data["totalPages"] == 3
    assert len(response.data["items"]) == 10


@pytest.mark.django_db
def test_health_endpoint(client):
    response = client.get("/health/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
