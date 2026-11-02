"""Shared permission helpers. The token-based IsAuthenticated lands with the accounts phase."""

from rest_framework.permissions import BasePermission


class IsOwner(BasePermission):
    """Object-level: the object's `user` must be the requester (callers should also scope querysets)."""

    def has_object_permission(self, request, view, obj):
        return getattr(obj, "user_id", None) == getattr(request.user, "id", None)
