from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.api_docs import VALIDATION_ERROR, error_response
from apps.content import selectors

from .serializers import (
    ArticleCategorySerializer,
    ArticleDetailSerializer,
    ArticleQuerySerializer,
    ArticleSerializer,
    BannerSerializer,
)


class BannerListView(APIView):
    @extend_schema(
        tags=["Content"],
        summary="Home hero slides",
        description="Active banners in display order. `secondaryCta` is omitted when not set.",
        responses=BannerSerializer(many=True),
    )
    def get(self, request):
        banners = selectors.active_banners()
        return Response(BannerSerializer(banners, many=True, context={"request": request}).data)


class ArticleListView(APIView):
    @extend_schema(
        tags=["Blog"],
        summary="Published blog articles",
        description="Newest first. Only published articles whose publish time has arrived; the body is not "
        "included (use the detail endpoint). Returns at most `limit` items (default and maximum 100).",
        parameters=[
            OpenApiParameter("limit", int, description="Most articles to return, 1-100 (default 100).")
        ],
        responses={200: ArticleSerializer(many=True), 400: VALIDATION_ERROR},
    )
    def get(self, request):
        query = ArticleQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        data = query.validated_data
        articles = selectors.published_articles(data["category"])[: data["limit"]]
        return Response(ArticleSerializer(articles, many=True, context={"request": request}).data)


class ArticleDetailView(APIView):
    @extend_schema(
        tags=["Blog"],
        summary="One published blog article",
        description="Includes `content`, the article body as sanitized HTML. Drafts, scheduled articles and "
        "unknown slugs all answer 404.",
        responses={200: ArticleDetailSerializer, 404: error_response("No published article with this slug.")},
    )
    def get(self, request, slug):
        article = selectors.get_published_article(slug)
        return Response(ArticleDetailSerializer(article, context={"request": request}).data)


class ArticleCategoryListView(APIView):
    @extend_schema(
        tags=["Blog"],
        summary="Blog categories",
        description="Active categories that have at least one published article, in display order, with the "
        "number of published articles. Use `slug` as the `category` filter of the article list.",
        responses=ArticleCategorySerializer(many=True),
    )
    def get(self, request):
        return Response(ArticleCategorySerializer(selectors.article_categories(), many=True).data)
