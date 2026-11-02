"""Registry of API versions: the single place to add or retire a version.

To add v2: create config/api/v2.py (its own `urlpatterns`), add "v2" below. v1 keeps working
untouched; feature apps can share views/serializers between versions or fork only what changed.
"""

API_VERSIONS: dict[str, str] = {
    "v1": "config.api.v1",
}
DEFAULT_API_VERSION = "v1"
