import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.catalog.models import Category, Product, ProductVariant
from apps.catalog.tests.conftest import add_image, make_product

pytestmark = pytest.mark.django_db


def test_seed_is_idempotent_and_matches_mock_shape(settings, api):
    settings.DEBUG = True
    call_command("seed_catalog")
    call_command("seed_catalog")
    assert Product.objects.count() == 12 and Category.objects.count() == 6 + 17
    assert ProductVariant.objects.filter(product__slug="atlas-rover-kit").count() == 3
    data = api.get("/api/v1/products", {"pageSize": 50}).data
    assert data["total"] == 12
    orbit = next(p for p in data["items"] if p["slug"] == "orbit-brain-board")
    assert orbit["inStock"] is False
    detail = api.get("/api/v1/products/atlas-rover-kit").data
    assert detail["variantLabel"] == "Size" and 2 <= len(detail["images"]) <= 5
    assert api.get("/api/v1/products", {"category": "robot-kits"}).data["total"] == 3


def test_seed_refuses_when_debug_off(settings):
    settings.DEBUG = False
    with pytest.raises(CommandError):
        call_command("seed_catalog")


def test_admin_stamps_created_and_modified_by(client, settings):
    User = get_user_model()
    staff = User.objects.create_superuser(
        email="boss@example.com", name="Boss", password="pass-12345!", mobile="9000000001"
    )
    client.force_login(staff)
    parent = Category.objects.create(name="Top", slug="top")
    response = client.post(
        "/admin/catalog/category/add/",
        {"name": "Sub", "slug": "sub", "parent": parent.pk, "sort_order": 0, "is_active": "on"},
    )
    assert response.status_code == 302
    created = Category.objects.get(slug="sub")
    assert created.created_by == staff and created.modified_by == staff


def test_admin_product_page_renders_with_media_inline(client):
    from .conftest import add_image, add_video, make_product

    staff = get_user_model().objects.create_superuser(
        email="boss2@example.com", name="Boss", password="pass-12345!", mobile="9000000002"
    )
    client.force_login(staff)
    product = make_product(image=False)
    add_image(product)
    add_video(product, position=1)
    response = client.get(f"/admin/catalog/product/{product.pk}/change/")
    assert response.status_code == 200
    assert b"Product media" in response.content


def test_variant_option_admin_crud_and_audit(client):
    from apps.catalog.models import VariantOption

    staff = get_user_model().objects.create_superuser(
        email="boss3@example.com", name="Boss", password="pass-12345!", mobile="9000000003"
    )
    client.force_login(staff)
    response = client.post(
        "/admin/catalog/variantoption/add/", {"title": "Crimson", "slug": "crimson", "type": "color"}
    )
    assert response.status_code == 302
    option = VariantOption.objects.get(slug="crimson")
    assert option.type == "color" and option.created_by == staff
    assert client.get("/admin/catalog/variantoption/").status_code == 200


@pytest.mark.django_db
class TestProductListImage:
    def login(self, client):
        admin = get_user_model().objects.create_superuser(
            email="root@example.com", name="Root", mobile="9000000001", password="s3cret-pass-1"
        )
        client.force_login(admin)

    def test_list_shows_the_main_image_or_a_dash(self, client):
        from django.urls import reverse

        with_image = make_product("Has image", image=False)
        add_image(with_image, position=5)
        main = add_image(with_image, position=1)
        make_product("No image", image=False)
        self.login(client)

        html = client.get(reverse("admin:catalog_product_changelist")).content.decode()
        assert main.file.url in html  # the lowest position wins
        assert (
            html.count("object-fit:cover") == 1
        )  # only the main one, and nothing for the product without images

    def test_list_does_not_query_media_per_row(self, client, django_assert_max_num_queries):
        from django.urls import reverse

        for n in range(6):
            add_image(make_product(f"P{n}", image=False))
        self.login(client)
        url = reverse("admin:catalog_product_changelist")
        client.get(url)  # warm up (content types, sessions)
        with django_assert_max_num_queries(12):
            assert client.get(url).status_code == 200


@pytest.mark.django_db
class TestProductFormLayout:
    def test_every_field_appears_once_in_one_ungrouped_layout(self, client):
        from django.contrib import admin
        from django.urls import reverse

        product = make_product("Layout", image=False)
        client.force_login(
            get_user_model().objects.create_superuser(
                email="root@example.com", name="Root", mobile="9000000001", password="s3cret-pass-1"
            )
        )
        model_admin = admin.site._registry[Product]
        assert len(model_admin.fieldsets) == 1 and model_admin.fieldsets[0][0] is None  # no section headings
        rows = model_admin.fieldsets[0][1]["fields"]
        laid_out = [n for row in rows for n in ([row] if isinstance(row, str) else row)]
        shown = {f.name for f in Product._meta.fields if f.name != "id"}
        assert sorted(laid_out) == sorted(shown)  # nothing missing, nothing twice

        page = client.get(reverse("admin:catalog_product_change", args=[product.pk]))
        html = page.content.decode()
        assert page.status_code == 200 and "form-multiline" in html
        assert html.count('data-richtext="1"') == 1 and 'name="description"' in html  # editor kept
        assert 'name="summary"' in html and 'name="price"' in html
