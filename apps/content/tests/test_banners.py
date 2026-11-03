import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.content.models import Banner
from apps.content.validators import validate_cta

URL = "/api/v1/banners"
GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,"
    b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)
PRIMARY = {"label": "Shop kits", "href": "/products?category=robot-kits"}


@pytest.fixture(autouse=True)
def _media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


def make_banner(title="Build your first robot", **kw):
    kw.setdefault("primary_cta", PRIMARY)
    kw.setdefault("image", SimpleUploadedFile("hero.png", GIF, content_type="image/png"))
    return Banner.objects.create(title=title, **kw)


@pytest.mark.django_db
class TestBannerApi:
    def test_public_shape_matches_the_contract(self):
        banner = make_banner(
            eyebrow="New season",
            description="Kits, sensors and guides in one place.",
            image_alt="Robot kit on a workbench",
            secondary_cta={"label": "Read guides", "href": "/blog"},
        )
        res = APIClient().get(URL)
        assert res.status_code == 200
        (item,) = res.data
        assert item["id"] == str(banner.pk)
        assert item["eyebrow"] == "New season"
        assert item["title"] == "Build your first robot"
        assert item["description"] == "Kits, sensors and guides in one place."
        assert item["image"].startswith("http") and item["image"].endswith(".png")
        assert item["imageAlt"] == "Robot kit on a workbench"
        assert item["primaryCta"] == PRIMARY
        assert item["secondaryCta"] == {"label": "Read guides", "href": "/blog"}

    def test_secondary_cta_is_omitted_when_not_set(self):
        make_banner()
        (item,) = APIClient().get(URL).data
        assert "secondaryCta" not in item

    def test_only_active_banners_in_sort_order(self):
        make_banner("Third", sort_order=3)
        make_banner("First", sort_order=1)
        make_banner("Hidden", sort_order=0, is_active=False)
        make_banner("Second", sort_order=2)
        assert [b["title"] for b in APIClient().get(URL).data] == ["First", "Second", "Third"]

    def test_empty_list(self):
        assert APIClient().get(URL).data == []

    def test_list_uses_a_constant_number_of_queries(self, django_assert_max_num_queries):
        for i in range(5):
            make_banner(f"B{i}")
        with django_assert_max_num_queries(2):
            APIClient().get(URL)


class TestCtaValidation:
    @pytest.mark.parametrize(
        "value",
        [
            {"label": "Shop", "href": "/products"},
            {"label": "Shop", "href": "/products?category=robot-kits&sort=price"},
            {"label": "Home", "href": "/"},
        ],
    )
    def test_valid(self, value):
        validate_cta(value)

    @pytest.mark.parametrize(
        "value",
        [
            None,
            "Shop",
            [],
            {},
            {"label": "Shop"},
            {"href": "/products"},
            {"label": "Shop", "href": "/products", "extra": 1},
            {"label": "", "href": "/products"},
            {"label": "   ", "href": "/products"},
            {"label": "x" * 61, "href": "/products"},
            {"label": 5, "href": "/products"},
            {"label": "Shop", "href": 5},
            {"label": "Shop", "href": "products"},
            {"label": "Shop", "href": "https://evil.example/x"},
            {"label": "Shop", "href": "//evil.example/x"},
            {"label": "Shop", "href": "javascript:alert(1)"},
            {"label": "Shop", "href": "/a b"},
            {"label": "Shop", "href": "/a\\b"},
            {"label": "Shop", "href": "/" + "a" * 300},
        ],
    )
    def test_invalid(self, value):
        with pytest.raises(ValidationError):
            validate_cta(value)

    @pytest.mark.django_db
    def test_model_clean_applies_the_validator_to_both_buttons(self):
        banner = Banner(title="T", primary_cta={"label": "Go", "href": "http://x"})
        banner.image = SimpleUploadedFile("h.png", GIF, content_type="image/png")
        with pytest.raises(ValidationError) as exc:
            banner.full_clean()
        assert "primary_cta" in exc.value.message_dict
        banner.primary_cta = PRIMARY
        banner.secondary_cta = {"label": "Go"}
        with pytest.raises(ValidationError) as exc:
            banner.full_clean()
        assert "secondary_cta" in exc.value.message_dict

    @pytest.mark.django_db
    def test_image_must_be_a_web_image(self):
        banner = Banner(title="T", primary_cta=PRIMARY)
        banner.image = SimpleUploadedFile("h.exe", b"x", content_type="application/octet-stream")
        with pytest.raises(ValidationError) as exc:
            banner.full_clean()
        assert "image" in exc.value.message_dict


@pytest.mark.django_db
def test_admin_can_add_and_list_banners(client):
    admin = User.objects.create_superuser(
        email="root@example.com", name="Root", mobile="9000000001", password="s3cret-pass-1"
    )
    client.force_login(admin)
    assert client.get(reverse("admin:content_banner_changelist")).status_code == 200
    assert client.get(reverse("admin:content_banner_add")).status_code == 200

    res = client.post(
        reverse("admin:content_banner_add"),
        {
            "title": "Drones",
            "is_active": "on",
            "sort_order": 1,
            "image": SimpleUploadedFile("d.png", GIF, content_type="image/png"),
            "primary_cta": '{"label": "Explore", "href": "/category/drones"}',
            "secondary_cta": "",
        },
    )
    assert res.status_code == 302, res.content.decode()[:500]
    banner = Banner.objects.get()
    assert banner.primary_cta == {"label": "Explore", "href": "/category/drones"}
    assert banner.secondary_cta is None
    assert banner.created_by == admin
