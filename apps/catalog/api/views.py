from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog import selectors
from apps.common.api_docs import VALIDATION_ERROR, error_response

from .serializers import (
    CategorySerializer,
    ProductDetailSerializer,
    ProductFiltersSerializer,
    ProductQuerySerializer,
    ProductSerializer,
    ProductSuggestionSerializer,
    SuggestionQuerySerializer,
)

NOT_FOUND = error_response("Unknown or inactive resource.")


def _validated_query(request) -> ProductQuerySerializer:
    params = ProductQuerySerializer(data=request.query_params)
    params.is_valid(raise_exception=True)
    return params


class CategoryListView(APIView):
    @extend_schema(tags=["Catalogue"], summary="Category tree", responses=CategorySerializer(many=True))
    def get(self, request):
        data = CategorySerializer(selectors.category_tree(), many=True, context={"request": request}).data
        return Response(data)


class CategoryDetailView(APIView):
    @extend_schema(
        tags=["Catalogue"],
        summary="Category or sub-category by slug",
        responses={200: CategorySerializer, 404: NOT_FOUND},
    )
    def get(self, request, slug):
        category = selectors.get_category(slug)
        if category is None:
            raise NotFound
        return Response(CategorySerializer(category, context={"request": request}).data)


class ProductListView(ListAPIView):
    serializer_class = ProductSerializer

    @extend_schema(
        tags=["Catalogue"],
        summary="List products (paginated, filterable)",
        description="`category` matches the category and, for a top-level one, all its sub-categories.",
        parameters=[ProductQuerySerializer],
        responses={400: VALIDATION_ERROR},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        params = _validated_query(self.request)
        return selectors.with_media(selectors.filter_products(**params.selector_kwargs()))


class ProductFiltersView(APIView):
    @extend_schema(
        tags=["Catalogue"],
        summary="Available filter values (price range) for a category / search",
        parameters=[ProductQuerySerializer],
        responses={200: ProductFiltersSerializer, 400: VALIDATION_ERROR},
    )
    def get(self, request):
        params = _validated_query(request).selector_kwargs()
        qs = selectors.filter_products(category=params["category"], q=params["q"])
        return Response(ProductFiltersSerializer({"priceRange": selectors.price_range(qs)}).data)


class ProductDetailView(RetrieveAPIView):
    serializer_class = ProductDetailSerializer
    lookup_field = "slug"
    queryset = None  # built per request through the selectors

    @extend_schema(tags=["Catalogue"], summary="Product detail", responses={404: NOT_FOUND})
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        return selectors.with_detail(selectors.storefront_products())


class RelatedProductsView(APIView):
    @extend_schema(
        tags=["Catalogue"],
        summary="Up to 4 related products",
        responses={200: ProductSerializer(many=True), 404: NOT_FOUND},
    )
    def get(self, request, slug):
        product = selectors.storefront_products().filter(slug=slug).first()
        if product is None:
            raise NotFound
        related = selectors.with_media(selectors.related_products(product))
        return Response(ProductSerializer(related, many=True, context={"request": request}).data)


class SearchSuggestionsView(APIView):
    @extend_schema(
        tags=["Catalogue"],
        summary="Search-as-you-type suggestions (max 5)",
        description="A blank or missing `q` returns an empty list.",
        parameters=[SuggestionQuerySerializer],
        responses=ProductSuggestionSerializer(many=True),
    )
    def get(self, request):
        query = SuggestionQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        items = selectors.suggestions(query.validated_data.get("q")).prefetch_related("media")
        return Response(ProductSuggestionSerializer(items, many=True, context={"request": request}).data)
