from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.tests.conftest import make_product
from apps.dashboard import selectors
from apps.orders.models import Order
from apps.orders.tests.test_orders_api import place

pytestmark = pytest.mark.django_db


@pytest.fixture
def superuser(db):
    return User.objects.create_superuser(
        email="root@example.com", name="Root", mobile="9000000001", password="s3cret-pass-1"
    )


def make_orders(api, count=1, price=100):
    product = make_product(price=price, stock_quantity=50)
    for _ in range(count):
        assert place(api, (product, 1)).status_code == 201
    return product


def set_status(status, created_at=None):
    qs = Order.objects.all()
    qs.update(status=status)
    if created_at:
        qs.update(created_at=created_at)


def test_revenue_counts_only_orders_that_are_really_placed(api):
    make_orders(api, count=4, price=100)
    first, second, third, fourth = Order.objects.order_by("created_at")
    Order.objects.filter(pk=first.pk).update(status="pending")
    Order.objects.filter(pk=second.pk).update(status="delivered")
    Order.objects.filter(pk=third.pk).update(status="cancelled")
    Order.objects.filter(pk=fourth.pk).update(status="awaiting_payment")

    totals = selectors.build_dashboard()["totals"]
    assert totals["revenue_all"] == Decimal(200)
    assert totals["orders_all"] == 2
    assert totals["revenue_today"] == Decimal(200)


def test_monthly_yearly_and_daily_series_are_complete_and_ordered(api):
    make_orders(api, count=2, price=100)
    last_year = timezone.now() - timedelta(days=400)
    first = Order.objects.order_by("created_at").first()
    Order.objects.filter(pk=first.pk).update(status="delivered", created_at=last_year)
    Order.objects.exclude(pk=first.pk).update(status="confirmed")

    data = selectors.build_dashboard()
    assert len(data["monthly"]) == selectors.MONTHS_SHOWN
    assert len(data["daily"]) == selectors.DAYS_SHOWN
    assert [bar.label for bar in data["yearly"]][-1] == str(timezone.localdate().year)
    assert data["monthly"][-1].revenue == Decimal(100)  # this month
    this_year = next(bar for bar in data["yearly"] if bar.label == str(timezone.localdate().year))
    last_year_bar = next(bar for bar in data["yearly"] if bar.label == str(last_year.year))
    assert this_year.revenue == Decimal(100) and last_year_bar.revenue == Decimal(100)
    assert max(bar.height for bar in data["monthly"]) == 100
    assert all(0 <= bar.height <= 100 for bar in data["daily"])


def test_empty_database_renders_zeros(superuser):
    data = selectors.build_dashboard()
    assert data["totals"]["revenue_all"] == 0 and data["month_change"] is None
    assert all(bar.height == 0 for bar in data["monthly"])


def test_dashboard_renders_for_a_superuser(client, superuser, api):
    make_orders(api, count=2, price=250)
    set_status("confirmed")
    client.force_login(superuser)

    response = client.get(reverse("admin:index"))
    assert response.status_code == 200
    html = response.content.decode()
    assert "Revenue this month" in html and "Monthly revenue" in html and "Yearly revenue" in html
    assert "₹500.00" in html
    assert "orders to accept" in html


def test_dashboard_figures_are_hidden_from_staff_who_cannot_view_orders(client, db):
    staff = User.objects.create_user(email="s@example.com", name="S", mobile="9000000002", is_staff=True)
    client.force_login(staff)
    response = client.get(reverse("admin:index"))
    assert response.status_code == 200
    assert "Revenue this month" not in response.content.decode()


def test_anonymous_users_are_sent_to_login(client):
    response = client.get(reverse("admin:index"))
    assert response.status_code == 302 and "/login/" in response["Location"]


def test_low_stock_and_attention_counts(superuser):
    make_product(name="Almost gone", stock_quantity=2)
    make_product(name="Plenty", stock_quantity=40)
    data = selectors.build_dashboard()
    assert data["attention"]["low_stock"] == 1
    assert [p.name for p in data["low_stock_products"]] == ["Almost gone"]


def test_sidebar_groups_models_into_sections_and_hides_nothing(client, superuser):
    from django.contrib import admin

    client.force_login(superuser)
    response = client.get(reverse("admin:orders_order_changelist"))
    sections = {
        s["name"]: [m["object_name"] for m in s["models"]] for s in response.context["available_apps"]
    }
    assert sections["Sales"] == ["Order", "Payment", "Cart"]
    assert sections["Customers"] == ["User", "Address"]
    assert "Banner" in sections["Content"]
    # every registered model shows up exactly once
    listed = [m for names in sections.values() for m in names]
    assert sorted(listed) == sorted(model.__name__ for model in admin.site._registry)
    html = response.content.decode()
    assert 'id="nav-sidebar"' in html and "Dashboard" in html


def test_an_apps_own_page_keeps_djangos_listing(client, superuser):
    client.force_login(superuser)
    response = client.get("/admin/orders/")
    assert response.status_code == 200
    assert [a["app_label"] for a in response.context["app_list"]] == ["orders"]


def test_staff_only_see_models_they_may_access(client, db):
    staff = User.objects.create_user(email="s@example.com", name="S", mobile="9000000002", is_staff=True)
    client.force_login(staff)
    assert client.get(reverse("admin:index")).context["available_apps"] == []


def test_admin_pages_load_the_brand_theme(client, superuser):
    from django.contrib.staticfiles import finders

    assert finders.find("admin/css/robohub-theme.css")
    client.force_login(superuser)
    assert "admin/css/robohub-theme.css" in client.get(reverse("admin:index")).content.decode()
    client.logout()
    assert "admin/css/robohub-theme.css" in client.get(reverse("admin:login")).content.decode()  # login too


def test_header_offers_every_palette_the_stylesheet_defines(client, superuser):
    from pathlib import Path

    from apps.dashboard.admin_site import PALETTES

    css = (Path(__file__).parents[1] / "static/admin/css/robohub-theme.css").read_text()
    client.force_login(superuser)
    html = client.get(reverse("admin:index")).content.decode()
    assert 'id="rh-palette"' in html and "admin/js/robohub-palette.js" in html
    for value, label in PALETTES:
        assert f'<option value="{value}">{label}</option>' in html
        assert f'data-palette="{value}"' in css, f"palette {value} has no colours in the stylesheet"


def test_sidebar_is_opened_while_parsing_so_pages_do_not_jump(client, superuser):
    client.force_login(superuser)
    for url in (reverse("admin:index"), reverse("admin:orders_order_changelist")):
        html = client.get(url).content.decode()
        script = html.index("navSidebarIsOpen")
        assert script < html.index('id="nav-sidebar"')  # runs before the sidebar is drawn
        assert 'classList.add("shifted")' in html


def test_theme_files_are_versioned_so_browsers_cannot_serve_a_stale_copy(client, superuser):
    import re

    client.force_login(superuser)
    html = client.get(reverse("admin:index")).content.decode()
    css = re.search(r'robohub-theme\.css\?v=([0-9a-f]+)"', html)
    js = re.search(r'robohub-palette\.js\?v=([0-9a-f]+)"', html)
    assert css and js and css.group(1) == js.group(1) and len(css.group(1)) == 12


def test_the_theme_version_changes_when_a_theme_file_changes(tmp_path, monkeypatch):
    from apps.dashboard import admin_site

    (tmp_path / "css").mkdir()
    (tmp_path / "js").mkdir()
    css, js = tmp_path / "css" / "robohub-theme.css", tmp_path / "js" / "robohub-palette.js"
    css.write_text("a")
    js.write_text("b")
    monkeypatch.setattr(admin_site, "STATIC_DIR", tmp_path)
    before = admin_site.theme_version()
    assert admin_site.theme_version() == before  # stable while nothing changes
    css.write_text("changed")
    assert admin_site.theme_version() != before


def test_the_form_grid_rules_are_in_the_page_not_in_a_cacheable_file(client, superuser):
    client.force_login(superuser)
    html = client.get(reverse("admin:index")).content.decode()
    assert "form-multiline" in html and "repeat(3, minmax(0, 1fr))" in html
