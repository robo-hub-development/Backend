from django.urls import path

from . import address_views as views

urlpatterns = [
    path("addresses", views.AddressListCreateView.as_view(), name="address-list"),
    path("addresses/<uuid:pk>", views.AddressDetailView.as_view(), name="address-detail"),
]
