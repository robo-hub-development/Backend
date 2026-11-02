from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.catalog.models import Category, Product, VariantOption

from .conftest import make_category, make_product, make_variant

pytestmark = pytest.mark.django_db


class TestCategoryTree:
    def test_two_levels_allowed(self):
        top = make_category("Drones")
        child = make_category("FPV", parent=top)
        assert child.top_level == top and top.top_level == top

    def test_third_level_rejected(self):
        top = make_category("Drones")
        child = make_category("FPV", parent=top)
        with pytest.raises(ValidationError):
            make_category("Racing", parent=child)

    def test_cannot_nest_a_category_that_has_children(self):
        a, b = make_category("A"), make_category("B")
        make_category("A1", parent=a)
        a.parent = b
        with pytest.raises(ValidationError):
            a.save()

    def test_category_cannot_be_own_parent(self):
        cat = make_category("Solo")
        cat.parent = cat
        with pytest.raises(ValidationError):
            cat.save()

    def test_db_blocks_self_parent_even_without_clean(self):
        cat = make_category("Solo")
        with pytest.raises(IntegrityError), transaction.atomic():
            Category.objects.filter(pk=cat.pk).update(parent=cat)

    def test_parent_with_products_cannot_be_deleted_through_child(self):
        child = make_category("Child", parent=make_category("Top"))
        make_product(category=child)
        with pytest.raises(Exception):  # noqa: B017 - ProtectedError
            child.delete()


class TestProductConstraints:
    def test_compare_at_must_exceed_price(self):
        product = make_product(price=100)
        with pytest.raises(IntegrityError), transaction.atomic():
            Product.objects.filter(pk=product.pk).update(compare_at_price=Decimal(100))
        Product.objects.filter(pk=product.pk).update(compare_at_price=Decimal(150))

    def test_negative_price_rejected(self):
        with pytest.raises(IntegrityError):
            make_product(price=-1)

    def test_rating_range(self):
        with pytest.raises(IntegrityError):
            make_product(rating=Decimal("9.9"))

    def test_reserved_slug_blocked(self):
        with pytest.raises(IntegrityError):
            Product.objects.create(name="F", slug="filters", category=make_category("C"), price=1)

    def test_in_stock_property(self):
        assert make_product(stock_quantity=1).in_stock
        assert not make_product(stock_quantity=0).in_stock

    def test_same_option_cannot_be_mapped_twice_to_a_product(self):
        product = make_product()
        make_variant(product, "Large", 200)
        with pytest.raises(IntegrityError):
            make_variant(product, "Large", 300)

    def test_one_master_option_is_shared_across_products(self):
        a, b = make_product("A"), make_product("B")
        make_variant(a, "Red", 10, type="color")
        make_variant(b, "Red", 20, type="color")
        assert VariantOption.objects.filter(title="Red").count() == 1
        assert VariantOption.objects.get(title="Red").product_variants.count() == 2


class TestVariantOptionMaster:
    def test_type_choices_are_size_and_color_only(self):
        assert [c[0] for c in VariantOption.Type.choices] == ["size", "color"]
        option = VariantOption(title="Huge", slug="huge", type="material")
        with pytest.raises(ValidationError):
            option.full_clean()

    def test_slug_and_title_unique_per_type_but_reusable_across_types(self):
        VariantOption.objects.create(title="Black", slug="black", type="color")
        VariantOption.objects.create(title="Black", slug="black", type="size")  # different type: allowed
        for kwargs in ({"title": "Other", "slug": "black"}, {"title": "Black", "slug": "other"}):
            with pytest.raises(IntegrityError), transaction.atomic():
                VariantOption.objects.create(type="color", **kwargs)

    def test_option_in_use_cannot_be_deleted(self):
        product = make_product()
        variant = make_variant(product, "Large", 100)
        with pytest.raises(Exception):  # noqa: B017 - ProtectedError
            variant.option.delete()


class TestProductMediaUpload:
    """Uploads are validated by content, not just extension; type is derived on save."""

    def _clean(self, product, name, content):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from apps.catalog.models import ProductMedia

        media = ProductMedia(product=product, file=SimpleUploadedFile(name, content))
        media.full_clean(exclude=["product"])
        media.save()
        return media

    def test_image_and_video_types_detected(self, settings, tmp_path):
        from .conftest import GIF, MP4, WEBM

        settings.MEDIA_ROOT = tmp_path
        product = make_product(image=False)
        assert self._clean(product, "a.gif", GIF).media_type == "image"
        assert self._clean(product, "b.mp4", MP4).media_type == "video"
        assert self._clean(product, "c.webm", WEBM).media_type == "video"

    @pytest.mark.parametrize(
        ("name", "content"),
        [
            ("evil.exe", b"MZ\x00\x00"),
            ("script.svg", b"<svg onload=alert(1)/>"),
            ("fake.png", b"not really a png"),
            ("fake.mp4", b"this is not a video at all"),
            ("fake.webm", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 20),
            ("noext", b"GIF89a"),
        ],
    )
    def test_rejects_unsupported_or_fake_files(self, name, content, settings, tmp_path):
        settings.MEDIA_ROOT = tmp_path
        with pytest.raises(ValidationError):
            self._clean(make_product(image=False), name, content)

    def test_size_limits(self, settings, tmp_path):
        from .conftest import GIF, MP4

        settings.MEDIA_ROOT = tmp_path
        settings.CATALOG_MAX_IMAGE_BYTES = 10
        settings.CATALOG_MAX_VIDEO_BYTES = 10
        product = make_product(image=False)
        with pytest.raises(ValidationError, match="too large"):
            self._clean(product, "a.gif", GIF)
        with pytest.raises(ValidationError, match="too large"):
            self._clean(product, "b.mp4", MP4)

    def test_editing_existing_media_does_not_revalidate_stored_file(self, settings, tmp_path):
        from .conftest import add_video

        settings.MEDIA_ROOT = tmp_path
        media = add_video(make_product(image=False))
        settings.CATALOG_MAX_VIDEO_BYTES = 1  # would fail if the stored file were re-read
        media.alt = "new title"
        media.full_clean(exclude=["product"])
