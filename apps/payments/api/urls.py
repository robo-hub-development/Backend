from django.urls import path

from . import views

urlpatterns = [
    # Declared before the <reference> route so "webhook" is never read as a reference.
    path("payments/webhook", views.WebhookView.as_view(), name="payment-webhook"),
    path("payments/verify", views.VerifyCheckoutView.as_view(), name="payment-verify"),
    path("payments/<str:reference>", views.PaymentStatusView.as_view(), name="payment-status"),
]
