from django.urls import path

from . import views

urlpatterns = [
    path("otp/send", views.OtpSendView.as_view(), name="auth-otp-send"),
    path("otp/verify", views.OtpVerifyView.as_view(), name="auth-otp-verify"),
    path("me", views.MeView.as_view(), name="auth-me"),
    path("logout", views.LogoutView.as_view(), name="auth-logout"),
]
