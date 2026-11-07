import hashlib
from functools import lru_cache
from pathlib import Path

from django.contrib import admin
from django.urls import reverse

from .selectors import build_dashboard

# The sidebar groups models by what staff do with them, not by Django app. Entries are "app_label.Model" or
# "app_label.*"; anything not listed lands in "Other", so a newly registered model is never hidden.
SECTIONS = [
    ("Sales", ["orders.Order", "payments.Payment", "cart.Cart"]),
    ("Catalog", ["catalog.Product", "catalog.Category", "catalog.VariantOption"]),
    ("Customers", ["accounts.User", "accounts.Address"]),
    ("Content", ["content.*"]),
    ("Settings", ["orders.CodTransitPolicy", "auth.Group", "payments.WebhookEvent"]),
]


# (value, label) for the header picker; the colours live in static/admin/css/robohub-theme.css.
PALETTES = [
    ("robohub", "Robo Hub (navy + orange)"),
    ("ocean", "Ocean (blue + cyan)"),
    ("forest", "Forest (green + amber)"),
    ("violet", "Violet (indigo + pink)"),
    ("graphite", "Graphite (grey + green)"),
]


STATIC_DIR = Path(__file__).parent / "static" / "admin"


@lru_cache(maxsize=8)
def _digest(path: str, mtime_ns: int) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:10]


def theme_version() -> str:
    """Changes whenever the theme CSS or JS changes. It is added to their URLs so browsers fetch the new file
    instead of serving an old cached copy (the dev server sends no cache headers, so browsers guess)."""
    files = [STATIC_DIR / "css" / "robohub-theme.css", STATIC_DIR / "js" / "robohub-palette.js"]
    return "".join(_digest(str(f), f.stat().st_mtime_ns) for f in files)[:12]


class RoboHubAdminSite(admin.AdminSite):
    site_header = "Stem Robo Hub"
    site_title = "Stem Robo Hub admin"
    index_title = "Dashboard"
    index_template = "admin/dashboard.html"

    def each_context(self, request):
        return {
            **super().each_context(request),
            "dashboard_url": reverse("admin:index"),
            "palettes": PALETTES,
            "theme_version": theme_version(),
        }

    def get_app_list(self, request, app_label=None):
        apps = super().get_app_list(request, app_label)
        if app_label is not None:  # an app's own page keeps Django's per-app listing
            return apps
        return regroup(apps)

    def index(self, request, extra_context=None):
        # Revenue is business-sensitive: only staff who may view orders get the dashboard figures.
        if request.user.has_perm("orders.view_order"):
            extra_context = {**(extra_context or {}), "dashboard": build_dashboard()}
        return super().index(request, extra_context)


def regroup(apps: list[dict]) -> list[dict]:
    """Rebuild Django's per-app list as the sections above, keeping only the models the user may see."""
    by_key, by_app = {}, {}
    for app in apps:
        for model in app["models"]:
            by_key[f"{app['app_label']}.{model['object_name']}"] = model
            by_app.setdefault(app["app_label"], []).append(f"{app['app_label']}.{model['object_name']}")

    placed, sections = set(), []
    for name, entries in [*SECTIONS, ("Other", [])]:
        keys = []
        for entry in entries:
            label, _, model = entry.partition(".")
            keys += by_app.get(label, []) if model == "*" else [entry]
        if name == "Other":
            keys = [key for key in by_key if key not in placed]
        models = [by_key[key] for key in keys if key in by_key and key not in placed]
        placed.update(keys)
        if models:
            sections.append(
                {
                    "name": name,
                    "app_label": name.lower(),
                    "app_url": models[0]["admin_url"] or "",
                    "has_module_perms": True,
                    "models": models,
                }
            )
    return sections
