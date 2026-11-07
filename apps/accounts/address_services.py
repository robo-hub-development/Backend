"""Saved delivery addresses. Writes lock the owner's user row so the address limit and the single-default rule
hold under concurrent requests."""

from django.db import transaction
from rest_framework.exceptions import NotFound, ValidationError

from .models import Address, User

MAX_ADDRESSES = 10  # keeps the list small enough to show without pagination and stops unbounded growth


def _lock_owner(user) -> None:
    User.objects.select_for_update().get(pk=user.pk)


def _clear_default(user, *, keep=None) -> None:
    others = Address.objects.filter(user=user, is_default=True)
    if keep is not None:
        others = others.exclude(pk=keep.pk)
    others.update(is_default=False)


def list_addresses(user):
    return Address.objects.filter(user=user)


def get_address(user, address_id) -> Address:
    # Foreign and unknown ids behave the same, so ids of other customers never leak.
    address = Address.objects.filter(user=user, pk=address_id).first()
    if address is None:
        raise NotFound
    return address


def create_address(user, *, is_default: bool = False, **fields) -> Address:
    """The first address is always the default; later ones become the default only when asked."""
    with transaction.atomic():
        _lock_owner(user)
        existing = Address.objects.filter(user=user).count()
        if existing >= MAX_ADDRESSES:
            raise ValidationError(
                {"_": f"You can save up to {MAX_ADDRESSES} addresses. Delete one to add another."}
            )
        make_default = is_default or existing == 0
        if make_default:
            _clear_default(user)
        return Address.objects.create(user=user, is_default=make_default, **fields)


def update_address(user, address_id, *, is_default: bool | None = None, **fields) -> Address:
    """`is_default=True` moves the default here. A default cannot be switched off: pick another instead."""
    with transaction.atomic():
        _lock_owner(user)
        address = get_address(user, address_id)
        for name, value in fields.items():
            setattr(address, name, value)
        if is_default is True:
            _clear_default(user, keep=address)
            address.is_default = True
        address.save()
        return address


def delete_address(user, address_id) -> None:
    """Deleting the default promotes the most recently added remaining address."""
    with transaction.atomic():
        _lock_owner(user)
        address = get_address(user, address_id)
        was_default = address.is_default
        address.delete()
        if was_default:
            successor = Address.objects.filter(user=user).order_by("-created_at").first()
            if successor is not None:
                successor.is_default = True
                successor.save(update_fields=["is_default", "updated_at"])
