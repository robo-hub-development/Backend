from django.conf import settings
from django.urls import get_resolver

from config.api.versions import API_VERSIONS


def test_every_registered_version_is_mounted_and_allowed():
    namespaces = set(get_resolver().namespace_dict)
    assert set(API_VERSIONS) <= namespaces
    assert tuple(API_VERSIONS) == settings.REST_FRAMEWORK["ALLOWED_VERSIONS"]


def test_unregistered_version_is_not_mounted():
    assert "v99" not in get_resolver().namespace_dict
