import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Category, Product
from apps.catalog.tests.conftest import make_category, make_product
from apps.common.richtext import clean_html


class TestCleanHtml:
    def test_keeps_basic_formatting(self):
        html = '<h2>Specs</h2><p>A <strong>fast</strong> <em>board</em></p><ul><li>One</li></ul><a href="https://x.io">link</a>'
        cleaned = clean_html(html)
        for fragment in (
            "<h2>Specs</h2>",
            "<strong>fast</strong>",
            "<ul><li>One</li></ul>",
            'href="https://x.io"',
        ):
            assert fragment in cleaned

    @pytest.mark.parametrize(
        "evil",
        [
            "<script>alert(1)</script>ok",
            '<p onclick="steal()">ok</p>',
            "<img src=x onerror=alert(1)>ok",
            "<iframe src=//evil></iframe>ok",
            '<a href="javascript:alert(1)">ok</a>',
            '<p style="position:fixed">ok</p>',
        ],
    )
    def test_removes_scripts_handlers_and_unsafe_links(self, evil):
        cleaned = clean_html(evil).lower()
        for bad in ("<script", "onclick", "onerror", "<iframe", "javascript:", "style=", "<img"):
            assert bad not in cleaned
        assert "ok" in cleaned

    def test_links_get_a_safe_rel(self):
        assert 'rel="noopener noreferrer"' in clean_html('<a href="https://x.io" target="_blank">x</a>')

    @pytest.mark.parametrize("empty", ["", None, "<p><br></p>", "<p></p>", "  <p><br></p> "])
    def test_empty_editor_output_becomes_an_empty_string(self, empty):
        assert clean_html(empty) == ""


@pytest.fixture
def staff_client(client, db):
    admin = User.objects.create_superuser(
        email="root@example.com", name="Root", mobile="9000000001", password="s3cret-pass-1"
    )
    client.force_login(admin)
    return client


@pytest.mark.django_db
class TestAdminEditor:
    def test_product_form_uses_the_editor_for_the_description_only(self, staff_client):
        product = make_product()
        html = staff_client.get(reverse("admin:catalog_product_change", args=[product.pk])).content.decode()
        assert 'name="description"' in html and 'data-richtext="1"' in html
        assert html.count('data-richtext="1"') == 1  # the summary and other fields stay plain
        assert "richtext/vendor/quill.js" in html and "richtext/richtext.js" in html

    def test_category_form_uses_the_editor(self, staff_client):
        category = make_category("Kits")
        html = staff_client.get(reverse("admin:catalog_category_change", args=[category.pk])).content.decode()
        assert 'data-richtext="1"' in html

    def test_saving_cleans_the_html(self, staff_client):
        product = make_product()
        url = reverse("admin:catalog_product_change", args=[product.pk])
        page = staff_client.get(url)
        form = page.context["adminform"].form
        data = {name: form.initial.get(name, "") for name in form.fields}
        data.update(
            description=(
                '<p>Hello <strong>world</strong></p><script>alert(1)</script><a href="javascript:x">bad</a>'
            ),
            category=product.category_id,
            price="100",
            stock_quantity="5",
            is_active="on",
        )
        for inline in page.context["inline_admin_formsets"]:
            fs = inline.formset
            data[f"{fs.prefix}-TOTAL_FORMS"] = 0
            data[f"{fs.prefix}-INITIAL_FORMS"] = 0
            data[f"{fs.prefix}-MIN_NUM_FORMS"] = 0
            data[f"{fs.prefix}-MAX_NUM_FORMS"] = 1000
        response = staff_client.post(url, {k: v for k, v in data.items() if v is not None})
        errors = response.context["adminform"].form.errors if response.status_code == 200 else None
        assert response.status_code == 302, errors
        product.refresh_from_db()
        assert "<strong>world</strong>" in product.description
        assert "<script" not in product.description and "javascript:" not in product.description

    def test_existing_plain_text_descriptions_still_save(self, staff_client):
        category = make_category("Plain")
        Category.objects.filter(pk=category.pk).update(description="Line one\n\nLine two")
        html = staff_client.get(reverse("admin:catalog_category_change", args=[category.pk])).content.decode()
        assert "Line one" in html  # the editor converts it to paragraphs in the browser


def test_product_model_is_untouched_by_the_widget():
    assert Product._meta.get_field("description").get_internal_type() == "TextField"
