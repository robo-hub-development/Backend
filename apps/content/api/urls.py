from django.urls import path

from . import views

urlpatterns = [
    path("banners", views.BannerListView.as_view(), name="banner-list"),
]
