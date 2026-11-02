"""Read-side queries for the public catalogue. Every list query is built to avoid N+1."""

from decimal import Decimal

from django.db.models import Case, Count, IntegerField, Max, Min, Prefetch, Q, QuerySet, Value, When

from .models import Category, Product, ProductVariant

SORTS: dict[str, tuple[str, ...]] = {
    "newest": ("-created_at", "id"),
    "bestselling": ("-sold_count", "-created_at", "id"),
    "price-asc": ("price", "id"),
    "price-desc": ("-price", "id"),
}
DEFAULT_SORT = "newest"
SUGGESTION_LIMIT = 5
RELATED_LIMIT = 4


def storefront_products() -> QuerySet[Product]:
    """Products visible to shoppers: active, in an active category whose parent is also active."""
    return (
        Product.objects.filter(is_active=True, category__is_active=True)
        .filter(Q(category__parent__isnull=True) | Q(category__parent__is_active=True))
        .select_related("category__parent")
    )


def _in_category_family(slug: str) -> Q:
    """The category itself plus, for a top-level category, everything below it."""
    return Q(category__slug=slug) | Q(category__parent__slug=slug)


def filter_products(
    *,
    category: str | None = None,
    q: str | None = None,
    min_price: Decimal | None = None,
    max_price: Decimal | None = None,
    sort: str = DEFAULT_SORT,
) -> QuerySet[Product]:
    qs = storefront_products()
    if category:
        qs = qs.filter(_in_category_family(category))
    if q and q.strip():
        term = q.strip()
        qs = qs.filter(Q(name__icontains=term) | Q(summary__icontains=term))
    if min_price is not None:
        qs = qs.filter(price__gte=min_price)
    if max_price is not None:
        qs = qs.filter(price__lte=max_price)
    return qs.order_by(*SORTS[sort])


def with_media(qs: QuerySet[Product]) -> QuerySet[Product]:
    return qs.prefetch_related("media")


def with_detail(qs: QuerySet[Product]) -> QuerySet[Product]:
    return qs.prefetch_related(
        "media", Prefetch("variants", queryset=ProductVariant.objects.select_related("option"))
    )


def price_range(qs: QuerySet[Product]) -> dict[str, Decimal]:
    agg = qs.order_by().aggregate(min=Min("price"), max=Max("price"))
    return {"min": agg["min"] or Decimal(0), "max": agg["max"] or Decimal(0)}


def related_products(product: Product) -> QuerySet[Product]:
    """Same category family first, then best sellers from elsewhere. One query."""
    family_id = product.category.top_level.id
    same_family = Q(category_id=family_id) | Q(category__parent_id=family_id)
    return (
        storefront_products()
        .exclude(pk=product.pk)
        .annotate(
            family_rank=Case(When(same_family, then=Value(0)), default=Value(1), output_field=IntegerField())
        )
        .order_by("family_rank", "-sold_count", "id")[:RELATED_LIMIT]
    )


def suggestions(q: str | None) -> QuerySet[Product]:
    if not q or not q.strip():
        return Product.objects.none()
    return filter_products(q=q, sort="bestselling")[:SUGGESTION_LIMIT]


def _product_counts() -> dict:
    rows = storefront_products().order_by().values("category_id").annotate(n=Count("id"))
    return {row["category_id"]: row["n"] for row in rows}


def category_tree() -> list[Category]:
    """Active top-level categories with `children` and `product_count` (own + descendants) attached."""
    active_children = Category.objects.filter(is_active=True)
    tops = list(
        Category.objects.filter(is_active=True, parent__isnull=True).prefetch_related(
            Prefetch("children", queryset=active_children, to_attr="active_children")
        )
    )
    counts = _product_counts()
    for top in tops:
        top.product_count = counts.get(top.id, 0) + sum(counts.get(c.id, 0) for c in top.active_children)
    return tops


def get_category(slug: str) -> Category | None:
    """Active category (top-level or sub) by slug with `product_count`, `active_children`, `parent` set."""
    category = Category.objects.select_related("parent").filter(slug=slug, is_active=True).first()
    if category is None or (category.parent_id and not category.parent.is_active):
        return None
    counts = _product_counts()
    category.active_children = (
        list(category.children.filter(is_active=True)) if not category.parent_id else []
    )
    category.product_count = counts.get(category.id, 0) + sum(
        counts.get(c.id, 0) for c in category.active_children
    )
    return category
