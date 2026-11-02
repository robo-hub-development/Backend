from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class StandardPagination(PageNumberPagination):
    """Envelope required by the storefront: {items, page, pageSize, total, totalPages}."""

    page_query_param = "page"
    page_size_query_param = "pageSize"
    page_size = 12
    max_page_size = 100

    def get_paginated_response(self, data):
        return Response(
            {
                "items": data,
                "page": self.page.number,
                "pageSize": self.page.paginator.per_page,
                "total": self.page.paginator.count,
                "totalPages": self.page.paginator.num_pages,
            }
        )

    def get_paginated_response_schema(self, schema):
        return {
            "type": "object",
            "properties": {
                "items": schema,
                "page": {"type": "integer"},
                "pageSize": {"type": "integer"},
                "total": {"type": "integer"},
                "totalPages": {"type": "integer"},
            },
        }
