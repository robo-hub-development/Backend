"""Staff never see raw UUIDs: every object has a readable label and no read-only page shows an "Id" row."""

import re

import pytest
from django.urls import reverse

from apps.accounts import services as account_services
from apps.accounts.models import Address, User
from apps.cart.models import Cart, CartItem
from apps.catalog.models import ProductVariant
from apps.catalog.tests.conftest import add_image, make_option, make_product
from apps.orders.models import Order
from apps.orders.tests.test_orders_api import place

pytestmark = pytest.mark.django_db

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


@pytest.fixture
def staff_client(client):
    admin = User.objects.create_superuser(
        email="root@example.com", name="Root", mobile="9000000001", password="s3cret-pass-1"
    )
    client.force_login(admin)
    return client


def test_labels_are_names_not_ids(member):
    product = make_product("ESP32 DevKit", image=False)
    media = add_image(product, position=2)
    variant = ProductVariant.objects.create(product=product, option=make_option("Large"), price=10)
    cart = Cart.objects.create(user=member)
    line = CartItem.objects.create(cart=cart, product=product, quantity=3)
    token, _ = account_services.issue_token(member)
    session = member.auth_tokens.get()
    address = Address.objects.create(
        user=member,
        full_name="Aarav",
        mobile="9876543210",
        line1="x",
        city="Pune",
        state="MH",
        pincode="411001",
    )

    assert str(media) == "ESP32 DevKit - Image 2"
    assert str(variant) == "ESP32 DevKit: Size: Large"
    assert str(cart) == "Cart of aarav@example.com"
    assert str(line) == "ESP32 DevKit x3"
    assert str(session) == "Login session of aarav@example.com"
    assert str(address) == "Aarav, Pune 411001"
    for text in map(str, (media, variant, cart, line, session, address)):
        assert not UUID.search(text), text


def test_admin_pages_show_no_uuid_text_or_id_row(staff_client, api, member):
    product = make_product("RPLidar", image=False)
    add_image(product)
    ProductVariant.objects.create(product=product, option=make_option("Large"), price=10)
    placed = place(api, (product, 1)).data["order"]
    order = Order.objects.get(pk=placed["id"])

    pages = [
        reverse("admin:orders_order_change", args=[order.pk]),
        reverse("admin:payments_payment_changelist"),
        reverse("admin:cart_cart_changelist"),
        reverse("admin:catalog_product_change", args=[product.pk]),
    ]
    cart = Cart.objects.get(user=member)
    pages.append(reverse("admin:cart_cart_change", args=[cart.pk]))
    for url in pages:
        html = staff_client.get(url).content.decode()
        visible = re.sub(
            r"(href|action|src|value|name|id|data-[a-z-]+)=\"[^\"]*\"", "", html
        )  # URLs are fine
        visible = re.sub(r"<script.*?</script>", "", visible, flags=re.S)
        assert not UUID.search(visible), f"{url} shows a raw UUID: {UUID.search(visible).group(0)}"
        assert 'class="field-id"' not in html, f"{url} has an Id row"


def test_order_inline_and_product_inlines_do_not_query_per_row(
    staff_client, api, member, django_assert_max_num_queries
):
    products = [make_product(f"P{n}", image=False) for n in range(5)]
    placed = place(api, *[(p, 1) for p in products], method="online")
    order_id = placed.data["order"]["id"]
    url = reverse("admin:orders_order_change", args=[order_id])
    staff_client.get(url)
    with django_assert_max_num_queries(30):
        assert staff_client.get(url).status_code == 200


def test_charge_rule_is_shown_in_words_not_json(staff_client, api, member):
    from apps.orders.models import CodTransitPolicy

    CodTransitPolicy.objects.create(scope="default", mode="fixed", value=40)
    placed = place(api, (make_product(price=500), 1)).data["order"]
    html = staff_client.get(reverse("admin:orders_order_change", args=[placed["id"]])).content.decode()
    assert "Rs 40.00 - everywhere" in html
    assert "policy_id" not in html
