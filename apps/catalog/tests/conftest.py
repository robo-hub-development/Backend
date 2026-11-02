from decimal import Decimal
from itertools import count

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils.text import slugify
from rest_framework.test import APIClient

from apps.catalog.models import Category, Product, ProductMedia, ProductVariant, VariantOption

_seq = count(1)
GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,"
    b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)


@pytest.fixture(autouse=True)
def _media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture
def api():
    return APIClient()


def make_category(name, parent=None, **kw):
    return Category.objects.create(name=name, slug=slugify(name), parent=parent, **kw)


def add_image(product, position=0, alt=""):
    return ProductMedia.objects.create(
        product=product,
        position=position,
        alt=alt,
        file=SimpleUploadedFile(f"{product.slug}-{position}.gif", GIF, content_type="image/gif"),
    )


def make_product(name=None, category=None, price=100, image=True, **kw):
    name = name or f"Product {next(_seq)}"
    category = category or make_category(f"Cat {next(_seq)}")
    kw.setdefault("stock_quantity", 5)
    product = Product.objects.create(
        name=name, slug=slugify(name), category=category, price=Decimal(price), **kw
    )
    if image:
        add_image(product)
    return product


def make_option(title, type="size"):
    option, _ = VariantOption.objects.get_or_create(type=type, slug=slugify(title), defaults={"title": title})
    return option


def make_variant(product, title, price, type="size", **kw):
    return ProductVariant.objects.create(
        product=product, option=make_option(title, type), price=Decimal(price), **kw
    )


MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 64
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 64


def add_video(product, position=0, alt="", name=None, content=MP4):
    name = name or f"{product.slug}-{position}.mp4"
    return ProductMedia.objects.create(
        product=product,
        position=position,
        alt=alt,
        file=SimpleUploadedFile(name, content, content_type="video/mp4"),
    )
