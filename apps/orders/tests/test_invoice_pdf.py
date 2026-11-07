from io import BytesIO

import pytest
from django.core import mail
from django.urls import reverse
from pypdf import PdfReader

from apps.accounts.models import User
from apps.catalog.tests.conftest import make_product
from apps.notifications import services
from apps.orders.invoice import invoice_context
from apps.orders.invoice_pdf import render_invoice_pdf
from apps.orders.models import CodTransitPolicy, Order, OrderItem

from .test_orders_api import place

pytestmark = pytest.mark.django_db


def text_of(pdf: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(BytesIO(pdf)).pages)


def pages_of(pdf: bytes) -> int:
    return len(PdfReader(BytesIO(pdf)).pages)


def make_order(api, name="ESP32 DevKit", price=500, **kwargs):
    product = make_product(name, price=price, stock_quantity=50)
    order = Order.objects.get(pk=place(api, (product, 1), **kwargs).data["order"]["id"])
    return Order.objects.select_related("user").prefetch_related("items", "charges").get(pk=order.pk)


def test_pdf_has_the_invoice_content_and_prints_amounts_as_rs(api):
    CodTransitPolicy.objects.create(scope="default", mode="fixed", value=40)
    order = make_order(api)
    pdf = render_invoice_pdf(invoice_context(order))

    assert pdf.startswith(b"%PDF") and pages_of(pdf) == 1
    text = text_of(pdf)
    for expected in (
        "STEM ROBO HUB INDIA",
        "INVOICE",
        f"INV-{order.order_number}",
        order.created_at.strftime("%d %b %Y"),
        "Aarav Mehta",
        "12 MG Road",
        "Bengaluru, Karnataka - 560001",
        "ESP32 DevKit",
        "Cash on delivery transit fee",
        "Rs. 540.00",
        "Rupees Five Hundred Forty Only",
        "CASH ON DELIVERY",
        "Authorised signatory",
    ):
        assert expected in text, expected


def test_text_outside_latin_is_replaced_not_fatal(api):
    order = make_order(
        api,
        address={
            "fullName": "रवि कुमार",
            "mobile": "9876543210",
            "line1": "12 सड़क, Block A",
            "city": "रायपुर",
            "state": "Chhattisgarh",
            "pincode": "492001",
        },
    )
    pdf = render_invoice_pdf(invoice_context(order))  # must not raise
    text = text_of(pdf)
    assert pdf.startswith(b"%PDF") and "Block A" in text and "Chhattisgarh" in text
    assert "रवि" not in text  # the standard fonts cannot draw Devanagari: it is printed as "?"


def test_no_font_files_are_bundled():
    from pathlib import Path

    import apps.orders.invoice_pdf as module

    assert not (Path(module.__file__).parent / "fonts").exists()


def test_gstin_courier_and_tracking_are_printed_when_set(api, settings):
    settings.SELLER = {**settings.SELLER, "gstin": "22AAAAA0000A1Z5"}
    order = make_order(api)
    order.courier_name, order.tracking_number = "Delhivery", "DL123456"
    text = text_of(render_invoice_pdf(invoice_context(order)))
    assert "22AAAAA0000A1Z5" in text and "Delhivery" in text and "DL123456" in text


def test_prepaid_invoice_has_no_cash_box(api):
    order = make_order(api, method="online")
    order.amount_paid, order.payment_status = order.total, "paid"
    text = text_of(render_invoice_pdf(invoice_context(order)))
    assert "CASH ON DELIVERY" not in text and "Paid online" in text


def test_untrusted_text_is_printed_literally_not_interpreted(api):
    order = make_order(
        api,
        address={
            "fullName": "<b>Bold</b> & <i>Co</i>",
            "mobile": "9876543210",
            "line1": "<script>alert(1)</script> 5 & 6 Street",
            "city": "Pune",
            "state": "MH",
            "pincode": "411001",
        },
    )
    OrderItem.objects.filter(order=order).update(product_name='Kit <&> "Pro"')
    order.refresh_from_db()
    order = Order.objects.prefetch_related("items", "charges").get(pk=order.pk)
    text = text_of(render_invoice_pdf(invoice_context(order)))
    assert "<b>Bold</b> & <i>Co</i>" in text and "<script>alert(1)</script> 5 & 6 Street" in text
    assert 'Kit <&> "Pro"' in text


def test_many_items_continue_on_more_pages_with_the_header_repeated(api):
    order = make_order(api)
    template = order.items.first()
    OrderItem.objects.bulk_create(
        OrderItem(
            order=order,
            product=None,
            product_name=f"Part number {n} with a fairly long descriptive name for wrapping",
            product_slug=f"part-{n}",
            unit_price=10,
            quantity=1,
        )
        for n in range(80)
    )
    order = Order.objects.prefetch_related("items", "charges").get(pk=order.pk)
    pdf = render_invoice_pdf(invoice_context(order))
    assert pages_of(pdf) >= 2
    pages = [page.extract_text() for page in PdfReader(BytesIO(pdf)).pages]
    assert "Part number 79" in text_of(pdf) and all("Authorised signatory" in page for page in pages)
    assert template.product_name in text_of(pdf)


def test_cancelled_orders_are_stamped(api):
    order = make_order(api)
    order.status = "cancelled"
    assert "do not ship" in text_of(render_invoice_pdf(invoice_context(order))).lower()


def test_the_same_invoice_gives_the_same_bytes(api):
    context = invoice_context(make_order(api))
    assert render_invoice_pdf(context) == render_invoice_pdf(context)


class TestInTheEmail:
    @pytest.fixture
    def commit(self, django_capture_on_commit_callbacks):
        return lambda: django_capture_on_commit_callbacks(execute=True)

    def test_the_invoice_email_carries_a_pdf(self, api, commit):
        with commit():
            order_id = place(api, (make_product("ESP32 DevKit", price=500), 1)).data["order"]["id"]
        mail.outbox.clear()
        assert services.send_invoice(order_id, resend=True).status == "sent"
        name, content, mimetype = mail.outbox[0].attachments[0]
        order = Order.objects.get(pk=order_id)
        assert (name, mimetype) == (f"Invoice-INV-{order.order_number}.pdf", "application/pdf")
        assert f"INV-{order.order_number}" in text_of(content) and "ESP32 DevKit" in text_of(content)
        assert "A PDF copy is attached" in mail.outbox[0].body

    def test_a_pdf_problem_still_sends_the_email_with_the_invoice_in_the_body(self, api, commit, monkeypatch):
        with commit():
            order_id = place(api, (make_product("ESP32 DevKit", price=500), 1)).data["order"]["id"]
        mail.outbox.clear()

        def boom(_context):
            raise RuntimeError("font missing")

        monkeypatch.setattr("apps.orders.invoice_pdf.render_invoice_pdf", boom)
        log = services.send_invoice(order_id, resend=True)
        assert log.status == "sent" and mail.outbox[0].attachments == []
        assert "ESP32 DevKit" in mail.outbox[0].body  # the whole invoice is in the message itself


class TestAdminDownload:
    @pytest.fixture
    def staff_client(self, client):
        client.force_login(
            User.objects.create_superuser(
                email="root@example.com", name="Root", mobile="9000000001", password="s3cret-pass-1"
            )
        )
        return client

    def test_staff_download_the_pdf(self, staff_client, api):
        order = make_order(api)
        response = staff_client.get(reverse("admin:orders_order_invoice_pdf", args=[order.pk]))
        assert response.status_code == 200 and response["Content-Type"] == "application/pdf"
        assert f"Invoice-INV-{order.order_number}.pdf" in response["Content-Disposition"]
        assert order.order_number in text_of(response.content)

    def test_the_print_page_links_to_the_pdf(self, staff_client, api):
        order = make_order(api)
        html = staff_client.get(reverse("admin:orders_order_invoice", args=[order.pk])).content.decode()
        assert reverse("admin:orders_order_invoice_pdf", args=[order.pk]) in html and "Download PDF" in html

    def test_a_bulk_print_page_has_no_single_pdf_link(self, staff_client, api):
        first, second = make_order(api, "Board A"), make_order(api, "Board B")
        response = staff_client.post(
            reverse("admin:orders_order_changelist"),
            {"action": "print_invoices", "_selected_action": [str(first.pk), str(second.pk)]},
        )
        assert "Download PDF" not in response.content.decode()

    def test_access_rules(self, client, api):
        order = make_order(api)
        url = reverse("admin:orders_order_invoice_pdf", args=[order.pk])
        assert client.get(url).status_code == 302
        client.force_login(
            User.objects.create_user(email="s@example.com", name="S", mobile="9000000002", is_staff=True)
        )
        assert client.get(url).status_code == 404

    def test_unknown_order_is_a_404(self, staff_client):
        ghost = reverse("admin:orders_order_invoice_pdf", args=["00000000-0000-0000-0000-000000000000"])
        assert staff_client.get(ghost).status_code == 404
