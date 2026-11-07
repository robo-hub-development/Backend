from decimal import Decimal

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.tests.conftest import make_product
from apps.orders.invoice import rupees_in_words
from apps.orders.models import CodTransitPolicy, Order

from .test_orders_api import place

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_client(client):
    admin = User.objects.create_superuser(
        email="root@example.com", name="Root", mobile="9000000001", password="s3cret-pass-1"
    )
    client.force_login(admin)
    return client


@pytest.mark.parametrize(
    ("amount", "words"),
    [
        (0, "Rupees Zero Only"),
        (1, "Rupees One Only"),
        (19, "Rupees Nineteen Only"),
        (40, "Rupees Forty Only"),
        (101, "Rupees One Hundred One Only"),
        (1234, "Rupees One Thousand Two Hundred Thirty Four Only"),
        (99999, "Rupees Ninety Nine Thousand Nine Hundred Ninety Nine Only"),
        (100000, "Rupees One Lakh Only"),
        (1234567, "Rupees Twelve Lakh Thirty Four Thousand Five Hundred Sixty Seven Only"),
        (12500000, "Rupees One Crore Twenty Five Lakh Only"),
        (Decimal("540.50"), "Rupees Five Hundred Forty and Fifty Paise Only"),
        (Decimal("0.05"), "Rupees Zero and Five Paise Only"),
    ],
)
def test_amount_in_words_uses_indian_numbering(amount, words):
    assert rupees_in_words(Decimal(amount)) == words


def _order(api, name="ESP32 DevKit", **kwargs):
    product = make_product(name, price=500)
    return Order.objects.get(pk=place(api, (product, 1), **kwargs).data["order"]["id"])


def test_invoice_has_the_standard_sections(staff_client, api):
    CodTransitPolicy.objects.create(scope="default", mode="fixed", value=40)
    order = _order(api)  # COD with a Rs 40 transit fee
    html = staff_client.get(reverse("admin:orders_order_invoice", args=[order.pk])).content.decode()

    for text in (
        "STEM ROBO HUB INDIA",
        f"INV-{order.order_number}",
        order.created_at.strftime("%d %b %Y"),
        "Bill to",
        "Ship to",
        "Aarav Mehta",
        "12 MG Road",
        "Bengaluru, Karnataka - 560001",
        "ESP32 DevKit",
        "Cash on delivery transit fee",
        "540.00",
        "Rupees Five Hundred Forty Only",
        "Authorised signatory",
        "computer-generated invoice",
    ):
        assert text in html, text
    assert "GSTIN" not in html  # only printed once configured


def test_cod_invoice_shows_the_cash_to_collect(staff_client, api):
    CodTransitPolicy.objects.create(scope="default", mode="fixed", value=40)
    order = _order(api)
    Order.objects.filter(pk=order.pk).update(amount_paid=40, payment_status="pending", status="confirmed")
    html = staff_client.get(reverse("admin:orders_order_invoice", args=[order.pk])).content.decode()
    assert "CASH ON DELIVERY" in html and "collect ₹ 500.00" in html


def test_prepaid_invoice_has_no_cash_box(staff_client, api):
    order = _order(api, method="online")
    Order.objects.filter(pk=order.pk).update(amount_paid=500, payment_status="paid", status="confirmed")
    html = staff_client.get(reverse("admin:orders_order_invoice", args=[order.pk])).content.decode()
    assert "CASH ON DELIVERY" not in html and "Paid online" in html


def test_gstin_and_tracking_are_printed_when_set(staff_client, api, settings):
    settings.SELLER = {**settings.SELLER, "gstin": "22AAAAA0000A1Z5"}
    order = _order(api)
    Order.objects.filter(pk=order.pk).update(courier_name="Delhivery", tracking_number="DL123")
    html = staff_client.get(reverse("admin:orders_order_invoice", args=[order.pk])).content.decode()
    assert "22AAAAA0000A1Z5" in html and "Delhivery" in html and "DL123" in html


def test_cancelled_orders_are_marked_do_not_ship(staff_client, api):
    order = _order(api)
    Order.objects.filter(pk=order.pk).update(status="cancelled")
    html = staff_client.get(reverse("admin:orders_order_invoice", args=[order.pk])).content.decode()
    assert "do not ship" in html


def test_bulk_action_prints_one_page_per_order(staff_client, api):
    first, second = _order(api, "Board A"), _order(api, "Board B")
    response = staff_client.post(
        reverse("admin:orders_order_changelist"),
        {"action": "print_invoices", "_selected_action": [str(first.pk), str(second.pk)]},
    )
    html = response.content.decode()
    assert response.status_code == 200
    assert html.count('<section class="page">') == 2
    assert f"INV-{first.order_number}" in html and f"INV-{second.order_number}" in html


def test_admin_links_to_the_invoice(staff_client, api):
    order = _order(api)
    url = reverse("admin:orders_order_invoice", args=[order.pk])
    assert url in staff_client.get(reverse("admin:orders_order_changelist")).content.decode()
    assert url in staff_client.get(reverse("admin:orders_order_change", args=[order.pk])).content.decode()


def test_invoice_needs_staff_with_view_permission(client, api, member):
    order = _order(api)
    url = reverse("admin:orders_order_invoice", args=[order.pk])
    assert client.get(url).status_code == 302  # not logged in: sent to the admin login
    staff = User.objects.create_user(email="s@example.com", name="S", mobile="9000000002", is_staff=True)
    client.force_login(staff)
    assert client.get(url).status_code == 404  # staff without order permission: nothing is revealed
    customer_client = type(client)()
    customer_client.force_login(member)
    assert customer_client.get(url).status_code == 302  # a customer is not staff at all


def test_unknown_order_is_a_404(staff_client):
    assert (
        staff_client.get(
            reverse("admin:orders_order_invoice", args=["00000000-0000-0000-0000-000000000000"])
        ).status_code
        == 404
    )
