from django.core.exceptions import ValidationError

CTA_LABEL_MAX = 60
CTA_HREF_MAX = 300


def _validate_site_path(href: str) -> None:
    """Links are site-relative ("/products?category=x"): no scheme, host or protocol-relative "//"."""
    if not href.startswith("/") or href.startswith("//") or "\\" in href or any(c.isspace() for c in href):
        raise ValidationError("href must be a site-relative path starting with a single '/', e.g. /products.")
    if len(href) > CTA_HREF_MAX:
        raise ValidationError(f"href must be at most {CTA_HREF_MAX} characters.")


def validate_cta(value) -> None:
    """A call-to-action button: exactly {"label": "<text>", "href": "/site/path"}."""
    if not isinstance(value, dict) or set(value) != {"label", "href"}:
        raise ValidationError('Use exactly {"label": "Shop kits", "href": "/products"}.')
    label, href = value["label"], value["href"]
    if not isinstance(label, str) or not label.strip() or len(label) > CTA_LABEL_MAX:
        raise ValidationError(f"label must be 1-{CTA_LABEL_MAX} characters.")
    if not isinstance(href, str):
        raise ValidationError("href must be text.")
    _validate_site_path(href)
