from django.urls import path

from . import views

urlpatterns = [
    path("orders", views.OrderListCreateView.as_view(), name="order-list"),
    path("orders/<uuid:pk>", views.OrderDetailView.as_view(), name="order-detail"),
    path("orders/<uuid:pk>/cancel", views.OrderCancelView.as_view(), name="order-cancel"),
]
