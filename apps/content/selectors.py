from django.db.models import Count, Q

from .models import Article, ArticleCategory, Banner


def active_banners():
    return Banner.objects.filter(is_active=True)


def _is_public():
    from django.utils import timezone

    return Q(is_published=True, published_at__lte=timezone.now())


def published_articles(category_slug: str | None = None):
    """Articles the public may read, newest first. The body is not loaded: lists never show it."""
    articles = Article.objects.filter(_is_public()).select_related("category")
    if category_slug:
        articles = articles.filter(category__slug=category_slug)
    return articles.order_by("-published_at", "-created_at").defer("body")


def article_categories():
    """Active categories that have at least one public article, with how many (for a filter bar)."""
    from django.utils import timezone

    public = Q(articles__is_published=True, articles__published_at__lte=timezone.now())
    return (
        ArticleCategory.objects.filter(is_active=True)
        .annotate(article_count=Count("articles", filter=public))
        .filter(article_count__gt=0)
        .order_by("sort_order", "name")  # Meta.ordering is ignored in grouped queries, so say it here
    )


def get_published_article(slug: str) -> Article:
    """One public article. Drafts, scheduled posts and unknown slugs are all the same 404."""
    from django.utils import timezone
    from rest_framework.exceptions import NotFound

    try:
        return Article.objects.select_related("category").get(
            slug=slug, is_published=True, published_at__lte=timezone.now()
        )
    except Article.DoesNotExist:
        raise NotFound from None
