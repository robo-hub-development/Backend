from django.urls import path

from . import views

urlpatterns = [
    path("cart", views.CartView.as_view(), name="cart"),
    path("cart/items/<uuid:pk>", views.CartItemView.as_view(), name="cart-item"),
]
