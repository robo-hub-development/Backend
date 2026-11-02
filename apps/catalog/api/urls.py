from django.urls import path

from . import views

urlpatterns = [
    path("categories", views.CategoryListView.as_view(), name="category-list"),
    path("categories/<slug:slug>", views.CategoryDetailView.as_view(), name="category-detail"),
    path("products", views.ProductListView.as_view(), name="product-list"),
    # Must stay before the <slug> route; "filters" is a reserved product slug.
    path("products/filters", views.ProductFiltersView.as_view(), name="product-filters"),
    path("products/<slug:slug>", views.ProductDetailView.as_view(), name="product-detail"),
    path("products/<slug:slug>/related", views.RelatedProductsView.as_view(), name="product-related"),
    path("search/suggestions", views.SearchSuggestionsView.as_view(), name="search-suggestions"),
]
