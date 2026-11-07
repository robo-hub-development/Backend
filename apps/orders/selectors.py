from rest_framework.exceptions import NotFound

from .models import Order


def user_orders(user):
    """The user's orders, newest first (the only way views reach orders: other users' rows never appear)."""
    return Order.objects.filter(user=user).prefetch_related("items", "charges", "payments")


def get_user_order(user, order_id) -> Order:
    try:
        return user_orders(user).get(pk=order_id)
    except Order.DoesNotExist:
        raise NotFound from None
