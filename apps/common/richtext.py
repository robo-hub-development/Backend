"""Rich text (HTML) written by staff in the admin. It is cleaned on save with an allow-list, so what reaches
the API and the storefront is limited to basic formatting: no scripts, styles, handlers or unsafe links."""

import html as html_lib
import re

import nh3

ALLOWED_TAGS = {
    "p", "br", "strong", "em", "u", "s", "h2", "h3", "h4", "ul", "ol", "li", "blockquote", "code", "pre", "a",
}  # fmt: skip
ALLOWED_ATTRIBUTES = {"a": {"href", "title", "target"}}
ALLOWED_SCHEMES = {"http", "https", "mailto", "tel"}
_EMPTY_EDITOR = {"<p><br></p>", "<p></p>"}  # what Quill leaves behind when everything is deleted


def clean_html(value: str) -> str:
    """Strip everything outside the allow-list; empty editor output becomes an empty string."""
    cleaned = nh3.clean(
        value or "",
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_SCHEMES,
        link_rel="noopener noreferrer",
    ).strip()
    return "" if cleaned in _EMPTY_EDITOR else cleaned


_BLOCK_END = re.compile(r"</(p|li|h[1-6]|blockquote|pre)>|<br\s*/?>", re.IGNORECASE)


def plain_text(value: str) -> str:
    """The visible text of an HTML fragment, with words kept apart and whitespace collapsed."""
    spaced = _BLOCK_END.sub(" ", value or "")
    text = html_lib.unescape(nh3.clean(spaced, tags=set(), attributes={}))
    return re.sub(r"\s+", " ", text).strip()
