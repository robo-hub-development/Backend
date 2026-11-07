from django.urls import path

from . import views

urlpatterns = [
    path("banners", views.BannerListView.as_view(), name="banner-list"),
    path("article-categories", views.ArticleCategoryListView.as_view(), name="article-category-list"),
    path("articles", views.ArticleListView.as_view(), name="article-list"),
    path("articles/<slug:slug>", views.ArticleDetailView.as_view(), name="article-detail"),
]
