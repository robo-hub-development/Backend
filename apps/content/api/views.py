from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.content import selectors

from .serializers import BannerSerializer


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
