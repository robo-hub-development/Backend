from django.urls import path

from . import views

urlpatterns = [
    path("orders", views.OrderListCreateView.as_view(), name="order-list"),
    path("checkout/quote", views.OrderQuoteView.as_view(), name="checkout-quote"),
    path("orders/<uuid:pk>", views.OrderDetailView.as_view(), name="order-detail"),
    path("orders/<uuid:pk>/pay", views.OrderPayView.as_view(), name="order-pay"),
    path("orders/<uuid:pk>/cancel", views.OrderCancelView.as_view(), name="order-cancel"),
]
