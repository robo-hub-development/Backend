import smtplib

import pytest
from django.core import mail
from django.core.management import call_command
from django.db import transaction
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.tests.conftest import make_product
from apps.notifications import services
from apps.notifications.models import EmailLog
from apps.orders import checkout
from apps.orders import services as order_services
from apps.orders.models import Order
from apps.orders.tests.test_orders_api import place
from apps.payments.tests.helpers import ORDERS, online_order, pay, send_webhook, set_cod_fee

pytestmark = pytest.mark.django_db


def pdf_text(data: bytes) -> str:
    """All text of a PDF (the tests read what the customer would see)."""
    from io import BytesIO

    from pypdf import PdfReader

    return "\n".join(page.extract_text() for page in PdfReader(BytesIO(data)).pages)


@pytest.fixture
def commit(django_capture_on_commit_callbacks):
    """Run on_commit hooks like a real commit would (pytest-django wraps tests in a transaction)."""

    def run():
        return django_capture_on_commit_callbacks(execute=True)

    return run


def refund_payload(event, refund_id="rfnd_1", payment_id="pay_77"):
    return {
        "event": event,
        "payload": {
            "refund": {"entity": {"id": refund_id, "payment_id": payment_id}},
            "payment": {"entity": {"id": payment_id}},
        },
    }


def kinds():
    return list(EmailLog.objects.order_by("created_at").values_list("kind", flat=True))


class TestOrderConfirmation:
    def test_cod_order_with_nothing_due_online_is_confirmed_at_once(self, api, member, commit):
        with commit():
            assert place(api, (make_product("ESP32 DevKit", price=500), 1)).status_code == 201

        assert len(mail.outbox) == 1
        email = mail.outbox[0]
        assert email.to == ["aarav@example.com"]
        order = Order.objects.get()
        assert order.order_number in email.subject
        body_html = email.alternatives[0][0]
        for text in ("ESP32 DevKit", "500.00", "Cash on delivery", order.order_number, "Aarav Mehta"):
            assert text in body_html or text in email.body, text
        assert "Pay in cash on delivery" in body_html
        assert email.alternatives[0][1] == "text/html" and email.body  # plain-text part too
        assert email.reply_to == ["stemrobohub@gmail.com"]
        log = EmailLog.objects.get()
        assert (log.kind, log.status, log.attempts, log.recipient) == (
            "order_confirmation",
            "sent",
            1,
            "aarav@example.com",
        )

    def test_online_order_is_confirmed_only_after_the_payment_arrives(self, api, commit):
        with commit():
            _, order, payment = online_order(api, price=250)
        assert mail.outbox == []  # created but unpaid: nothing to confirm yet

        with commit():
            pay(payment)
        assert len(mail.outbox) == 1 and order.order_number in mail.outbox[0].subject

    def test_replayed_webhook_does_not_email_twice(self, api, commit):
        _, _, payment = online_order(api)
        with commit():
            pay(payment, event_id="evt_1")
        with commit():
            pay(payment, event_id="evt_1")  # same delivery again
            pay(payment, event_id="evt_2")  # a different event for the same payment
        assert len(mail.outbox) == 1 and kinds() == ["order_confirmation"]

    def test_cod_with_a_fee_is_confirmed_when_the_fee_is_paid(self, api, commit):
        set_cod_fee(40)
        with commit():
            res = place(api, (make_product(price=500), 1))
        assert res.status_code == 201 and mail.outbox == []
        order = Order.objects.get()
        with commit():
            pay(order.payments.get(purpose="cod_fee"))
        assert len(mail.outbox) == 1
        assert "Pay in cash on delivery" in mail.outbox[0].alternatives[0][0]

    def test_a_rolled_back_order_sends_nothing(self, api, commit):
        product = make_product(price=500, stock_quantity=0)
        with commit():
            assert place(api, (product, 1)).status_code == 400  # out of stock
        assert mail.outbox == [] and EmailLog.objects.count() == 0

    def test_the_customers_name_is_escaped_in_the_html(self, api, member, commit):
        member.name = "<script>alert(1)</script>"
        member.save()
        with commit():
            place(
                api,
                (make_product(price=500), 1),
                address={
                    "fullName": "<b>Bold</b>",
                    "mobile": "9876543210",
                    "line1": "1 Street",
                    "city": "Pune",
                    "state": "MH",
                    "pincode": "411001",
                },
            )
        html = mail.outbox[0].alternatives[0][0]
        assert "<b>Bold</b>" not in html and "&lt;b&gt;Bold&lt;/b&gt;" in html


class TestRefundEmails:
    def cancelled(self, api, commit):
        _, order, payment = online_order(api, price=250)
        with commit():
            pay(payment, payment_id="pay_77")
        mail.outbox.clear()
        with commit():
            assert api.post(f"{ORDERS}/{order.pk}/cancel").status_code == 200
        payment.refresh_from_db()
        return order, payment

    def test_cancelling_a_paid_order_emails_that_the_refund_started(self, api, commit):
        order, payment = self.cancelled(api, commit)
        assert len(mail.outbox) == 1
        email = mail.outbox[0]
        assert "refund" in email.subject.lower() and order.order_number in email.subject
        assert "250.00" in email.body and payment.gateway_refund_id in email.body
        assert kinds()[-1] == "refund_started"

    def test_the_confirmed_refund_emails_once(self, api, commit):
        order, _ = self.cancelled(api, commit)
        mail.outbox.clear()
        with commit():
            assert send_webhook(refund_payload("refund.processed"), event_id="evt_rp").status_code == 200
        with commit():
            send_webhook(refund_payload("refund.processed"), event_id="evt_rp")  # replay
            send_webhook(refund_payload("refund.processed"), event_id="evt_rp2")  # same refund, new event
        assert len(mail.outbox) == 1
        assert "complete" in mail.outbox[0].subject.lower() and "250.00" in mail.outbox[0].body
        assert kinds()[-2:] == ["refund_started", "refund_completed"]

    def test_a_failed_refund_sends_no_completion_email(self, api, commit):
        self.cancelled(api, commit)
        mail.outbox.clear()
        with commit():
            send_webhook(refund_payload("refund.failed"), event_id="evt_rf")
        assert mail.outbox == []

    def test_cancelling_an_unpaid_order_sends_no_refund_email(self, api, commit):
        _, order, _ = online_order(api)
        with commit():
            assert api.post(f"{ORDERS}/{order.pk}/cancel").status_code == 200
        assert mail.outbox == []

    def test_a_refund_the_gateway_rejects_sends_no_email(self, api, gateway, commit):
        gateway.fail_refund = True
        _, order, payment = online_order(api)
        with commit():
            pay(payment, payment_id="pay_77")
        mail.outbox.clear()
        with commit():
            assert api.post(f"{ORDERS}/{order.pk}/cancel").status_code == 200
        assert mail.outbox == []


class TestInvoiceEmail:
    def shipped(self, api, commit):
        _, order, payment = online_order(api, price=250)
        with commit():
            pay(payment)
        mail.outbox.clear()
        with commit(), transaction.atomic():
            locked = Order.objects.select_for_update().get(pk=order.pk)
            order_services.transition(locked, "shipped", reason="handed to courier")
        return order

    def test_shipping_emails_the_invoice_as_an_attachment(self, api, commit):
        order = self.shipped(api, commit)
        assert len(mail.outbox) == 2  # the "on its way" notice and the invoice
        email = next(m for m in mail.outbox if m.attachments)
        assert f"INV-{order.order_number}" in email.subject
        name, content, mimetype = email.attachments[0]
        assert name == f"Invoice-INV-{order.order_number}.pdf" and mimetype == "application/pdf"
        assert content.startswith(b"%PDF")
        text = pdf_text(content)
        assert "INVOICE" in text and "Authorised signatory" in text and "250.00" in text

    def test_invoice_is_sent_once_automatically_but_staff_can_resend(self, api, commit):
        order = self.shipped(api, commit)
        assert services.send_invoice(order.pk) is None  # automatic sends are deduplicated
        log = services.send_invoice(order.pk, resend=True)
        assert log.status == "sent" and len(mail.outbox) == 3  # shipped notice, invoice, and the resend


class TestFailures:
    def break_mail(self, monkeypatch):
        def boom(self, fail_silently=False):
            raise smtplib.SMTPException("mail server down")

        monkeypatch.setattr("django.core.mail.EmailMultiAlternatives.send", boom)

    def test_a_mail_outage_never_breaks_the_order(self, api, member, commit, monkeypatch):
        self.break_mail(monkeypatch)
        with commit():
            res = place(api, (make_product(price=500), 1))
        assert res.status_code == 201 and Order.objects.count() == 1
        log = EmailLog.objects.get()
        assert (log.status, log.attempts) == ("failed", 1)
        assert "SMTPException" in log.error and "mail server down" in log.error

    def test_failed_emails_are_retried_and_then_stop(self, api, member, commit, monkeypatch):
        with monkeypatch.context() as broken:
            self.break_mail(broken)
            with commit():
                place(api, (make_product(price=500), 1))
        log = EmailLog.objects.get()
        assert log.status == "failed" and mail.outbox == []

        call_command("retry_failed_emails")  # mail works again
        log.refresh_from_db()
        assert (log.status, log.attempts, log.error) == ("sent", 2, "") and len(mail.outbox) == 1

        call_command("retry_failed_emails")  # nothing left to retry
        assert len(mail.outbox) == 1

    def test_retries_give_up_after_the_attempt_limit(self, api, member, commit, monkeypatch):
        self.break_mail(monkeypatch)
        with commit():
            place(api, (make_product(price=500), 1))
        for _ in range(5):
            call_command("retry_failed_emails")
        log = EmailLog.objects.get()
        assert (log.status, log.attempts) == ("failed", services.MAX_ATTEMPTS)

    def test_notifications_can_be_switched_off(self, api, member, commit, settings):
        settings.NOTIFICATIONS_ENABLED = False
        with commit():
            place(api, (make_product(price=500), 1))
        assert mail.outbox == [] and EmailLog.objects.count() == 0


class TestAdmin:
    @pytest.fixture
    def staff_client(self, client):
        admin = User.objects.create_superuser(
            email="root@example.com", name="Root", mobile="9000000001", password="s3cret-pass-1"
        )
        client.force_login(admin)
        return client

    def test_staff_can_email_the_invoice_from_the_order_page(self, staff_client, api, commit):
        with commit():
            order_id = place(api, (make_product(price=500), 1)).data["order"]["id"]
        mail.outbox.clear()
        url = reverse("admin:orders_order_email_invoice", args=[order_id])

        assert staff_client.get(url).status_code == 405  # a link click must not send anything
        assert mail.outbox == []
        assert staff_client.post(url).status_code == 302
        assert len(mail.outbox) == 1 and mail.outbox[0].attachments
        page = staff_client.get(reverse("admin:orders_order_change", args=[order_id])).content.decode()
        assert "Email invoice to customer" in page and "Emails sent to the customer" in page

    def test_bulk_action_emails_each_selected_customer(self, staff_client, api, commit):
        with commit():
            ids = [
                place(api, (make_product(name, price=500), 1)).data["order"]["id"]
                for name in ("Board A", "Board B")
            ]
        mail.outbox.clear()
        response = staff_client.post(
            reverse("admin:orders_order_changelist"),
            {"action": "email_invoices", "_selected_action": ids},
            follow=True,
        )
        assert response.status_code == 200 and len(mail.outbox) == 2

    def test_email_log_is_read_only_in_the_admin(self, staff_client, api, commit):
        with commit():
            place(api, (make_product(price=500), 1))
        log = EmailLog.objects.get()
        assert staff_client.get(reverse("admin:notifications_emaillog_changelist")).status_code == 200
        assert (
            staff_client.get(reverse("admin:notifications_emaillog_change", args=[log.pk])).status_code == 200
        )
        assert staff_client.get(reverse("admin:notifications_emaillog_add")).status_code == 403
        delete = reverse("admin:notifications_emaillog_delete", args=[log.pk])
        assert staff_client.post(delete, {"post": "yes"}).status_code == 403

    def test_staff_without_permission_cannot_trigger_emails(self, client, api, commit):
        with commit():
            order_id = place(api, (make_product(price=500), 1)).data["order"]["id"]
        mail.outbox.clear()
        staff = User.objects.create_user(email="s@example.com", name="S", mobile="9000000002", is_staff=True)
        client.force_login(staff)
        assert client.post(reverse("admin:orders_order_email_invoice", args=[order_id])).status_code == 404
        assert mail.outbox == []


class TestDeduplication:
    """The EmailLog claim is the last line of defence even if a caller fires an event twice."""

    def test_the_same_event_fired_twice_sends_one_email(self, api, member, commit):
        with commit():
            order_id = place(api, (make_product(price=500), 1)).data["order"]["id"]
        services.notify_order_confirmed(order_id)
        services.notify_order_confirmed(order_id)
        assert len(mail.outbox) == 1 and EmailLog.objects.filter(kind="order_confirmation").count() == 1

    def test_each_refund_is_claimed_per_payment(self, api, commit):
        _, order, payment = online_order(api, price=250)
        with commit():
            pay(payment, payment_id="pay_77")
        with commit():
            api.post(f"{ORDERS}/{order.pk}/cancel")
        mail.outbox.clear()
        services.notify_refund_started(payment.pk)  # already sent when the cancel committed
        services.notify_refund_completed(payment.pk)
        services.notify_refund_completed(payment.pk)
        assert len(mail.outbox) == 1 and "complete" in mail.outbox[0].subject.lower()

    def test_a_concurrent_second_claim_loses_cleanly(self, api, member, commit):
        with commit():
            order_id = place(api, (make_product(price=500), 1)).data["order"]["id"]
        order = Order.objects.get(pk=order_id)
        assert services._claim(order, "order_confirmation", None, "order_confirmation:x") is not None
        assert services._claim(order, "order_confirmation", None, "order_confirmation:x") is None


class TestDeliveryEmails:
    """The customer hears when a delivery partner is assigned and when the order ships, with the details."""

    @pytest.fixture
    def staff_client(self, client):
        admin = User.objects.create_superuser(
            email="root@example.com", name="Root", mobile="9000000001", password="s3cret-pass-1"
        )
        client.force_login(admin)
        return client

    def confirmed(self, api, commit, *, price=500):
        product = make_product(price=price, stock_quantity=5)
        with commit():
            order = Order.objects.get(pk=place(api, (product, 1)).data["order"]["id"])
            Order.objects.filter(pk=order.pk).update(status="confirmed")
        mail.outbox.clear()
        return Order.objects.get(pk=order.pk)

    def edit_details(self, staff_client, order, **fields):
        url = reverse("admin:orders_order_change", args=[order.pk])
        page = staff_client.get(url)
        data = {"courier_name": order.courier_name, "tracking_number": order.tracking_number, **fields}
        for inline in page.context["inline_admin_formsets"]:
            fs = inline.formset
            data.update(
                {
                    f"{fs.prefix}-TOTAL_FORMS": len(fs.forms),
                    f"{fs.prefix}-INITIAL_FORMS": len(fs.forms),
                    f"{fs.prefix}-MIN_NUM_FORMS": 0,
                    f"{fs.prefix}-MAX_NUM_FORMS": 1000,
                }
            )
            for index, form in enumerate(fs.forms):
                data[f"{fs.prefix}-{index}-id"] = str(form.instance.pk)
                data[f"{fs.prefix}-{index}-{fs.fk.name}"] = str(order.pk)
        response = staff_client.post(url, data)
        assert response.status_code == 302, response.context["adminform"].form.errors
        order.refresh_from_db()

    def test_shipping_emails_the_partner_and_tracking_number(self, api, commit):
        order = self.confirmed(api, commit)
        Order.objects.filter(pk=order.pk).update(courier_name="Delhivery", tracking_number="DL123456")
        with commit(), transaction.atomic():
            order_services.transition(Order.objects.select_for_update().get(pk=order.pk), "shipped")

        email = next(m for m in mail.outbox if "on its way" in m.subject)
        assert order.order_number in email.subject and email.to == ["aarav@example.com"]
        for text in ("Delhivery", "DL123456", "Pay in cash on delivery", "500.00"):
            assert text in email.alternatives[0][0], text
        assert "Delhivery" in email.body and "DL123456" in email.body
        assert "keep" in email.alternatives[0][0] and "ready" in email.alternatives[0][0]  # COD cash note

    def test_shipping_without_tracking_says_details_will_follow(self, api, commit):
        order = self.confirmed(api, commit)
        with commit(), transaction.atomic():
            order_services.transition(Order.objects.select_for_update().get(pk=order.pk), "shipped")
        email = next(m for m in mail.outbox if "on its way" in m.subject)
        assert "tracking details as soon as we have them" in email.body

    def test_the_shipped_email_is_sent_once(self, api, commit):
        order = self.confirmed(api, commit)
        with commit(), transaction.atomic():
            order_services.transition(Order.objects.select_for_update().get(pk=order.pk), "shipped")
        mail.outbox.clear()
        services.notify_order_shipped(order.pk)
        assert mail.outbox == []

    def test_assigning_a_partner_before_dispatch_emails_the_customer(self, staff_client, api, commit):
        order = self.confirmed(api, commit)
        with commit():
            self.edit_details(staff_client, order, courier_name="Delhivery", tracking_number="DL777")
        assert len(mail.outbox) == 1
        email = mail.outbox[0]
        assert "Delivery details" in email.subject
        assert "Delhivery" in email.body and "DL777" in email.body
        assert "dispatched" in email.body  # not shipped yet: we say we will email again

    def test_details_on_a_shipped_order_use_the_on_its_way_wording(self, staff_client, api, commit):
        order = self.confirmed(api, commit)
        Order.objects.filter(pk=order.pk).update(status="shipped")
        order.refresh_from_db()
        with commit():
            self.edit_details(staff_client, order, tracking_number="DL888")
        assert "on its way" in mail.outbox[0].body

    def test_saving_the_same_details_again_does_not_email_again(self, staff_client, api, commit):
        order = self.confirmed(api, commit)
        with commit():
            self.edit_details(staff_client, order, courier_name="Delhivery", tracking_number="DL777")
        with commit():
            self.edit_details(staff_client, order, courier_name="Delhivery", tracking_number="DL777")
        assert len(mail.outbox) == 1

    def test_a_corrected_tracking_number_is_sent_again(self, staff_client, api, commit):
        order = self.confirmed(api, commit)
        with commit():
            self.edit_details(staff_client, order, courier_name="Delhivery", tracking_number="WRONG")
        with commit():
            self.edit_details(staff_client, order, tracking_number="DL999")
        assert len(mail.outbox) == 2 and "DL999" in mail.outbox[1].body

    def test_changing_nothing_sends_nothing(self, staff_client, api, commit):
        order = self.confirmed(api, commit)
        with commit():
            self.edit_details(staff_client, order)
        assert mail.outbox == []

    def test_clearing_the_details_sends_nothing(self, staff_client, api, commit):
        order = self.confirmed(api, commit)
        with commit():
            self.edit_details(staff_client, order, courier_name="Delhivery", tracking_number="DL777")
        mail.outbox.clear()
        with commit():
            self.edit_details(staff_client, order, courier_name="", tracking_number="")
        assert mail.outbox == []

    def test_a_cancelled_order_gets_no_delivery_email(self, staff_client, api, commit):
        order = self.confirmed(api, commit)
        Order.objects.filter(pk=order.pk).update(status="cancelled")
        order.refresh_from_db()
        with commit():
            self.edit_details(staff_client, order, courier_name="Delhivery", tracking_number="DL777")
        assert mail.outbox == []

    def test_shipping_from_the_status_panel_sends_one_notice_not_two(self, staff_client, api, commit):
        order = self.confirmed(api, commit)
        with commit():
            staff_client.post(
                reverse("admin:orders_order_status", args=[order.pk]),
                {"action": "ship", "courier_name": "Delhivery", "tracking_number": "DL555"},
            )
        kinds_sent = sorted(
            EmailLog.objects.exclude(kind="order_confirmation").values_list("kind", flat=True)
        )
        assert kinds_sent == ["invoice", "order_shipped"]  # no separate "delivery details" email


class TestDeliveredEmail:
    def delivered(self, api, commit, *, method="cod"):
        product = make_product("ESP32 DevKit", price=500, stock_quantity=5)
        with commit():
            if method == "online":
                _, order, payment = online_order(api, price=500)
                pay(payment)
            else:
                order = Order.objects.get(pk=place(api, (product, 1)).data["order"]["id"])
                Order.objects.filter(pk=order.pk).update(status="shipped")
        Order.objects.filter(pk=order.pk).update(status="shipped")
        mail.outbox.clear()
        with commit():
            checkout.mark_delivered(order.pk)
        return Order.objects.get(pk=order.pk)

    def test_delivery_emails_the_customer_once(self, api, commit):
        order = self.delivered(api, commit)
        emails = [m for m in mail.outbox if "delivered" in m.subject]
        assert len(emails) == 1 and order.order_number in emails[0].subject
        assert emails[0].to == ["aarav@example.com"]
        html = emails[0].alternatives[0][0]
        assert "has been delivered" in html and "ESP32 DevKit" in html and "500.00" in html
        assert "cash was collected on delivery" in html and "has been delivered" in emails[0].body
        mail.outbox.clear()
        services.notify_order_delivered(order.pk)  # fired again: deduplicated
        assert mail.outbox == []

    def test_prepaid_orders_say_the_payment_was_received_online(self, api, commit):
        self.delivered(api, commit, method="online")
        email = next(m for m in mail.outbox if "delivered" in m.subject)
        assert "received online" in email.alternatives[0][0]

    def test_no_delivered_email_for_other_status_changes(self, api, commit):
        product = make_product(price=500, stock_quantity=5)
        with commit():
            order = Order.objects.get(pk=place(api, (product, 1)).data["order"]["id"])
        mail.outbox.clear()
        with commit():
            checkout.confirm_order(order.pk)
        assert not [m for m in mail.outbox if "delivered" in m.subject]


class TestInvoiceInTheEmailBody:
    def sent(self, api, commit):
        from apps.orders.models import CodTransitPolicy

        CodTransitPolicy.objects.create(scope="default", mode="fixed", value=40)
        with commit():
            order = Order.objects.get(
                pk=place(api, (make_product("ESP32 DevKit", price=500), 1)).data["order"]["id"]
            )
            pay(order.payments.get(purpose="cod_fee"))
        mail.outbox.clear()
        log = services.send_invoice(order.pk, resend=True)
        assert log.status == "sent"
        return order, mail.outbox[0]

    def test_the_invoice_is_in_the_email_itself_and_attached(self, api, commit):
        order, email = self.sent(api, commit)
        html, text = email.alternatives[0][0], email.body
        for part in (html, text):
            for expected in (
                f"INV-{order.order_number}",
                "STEM ROBO HUB INDIA",
                "Aarav Mehta",
                "12 MG Road",
                "ESP32 DevKit",
                "Cash on delivery transit fee",
                "540.00",
                "Rupees Five Hundred Forty Only",
            ):
                assert expected in part, expected
        assert "Cash to pay on delivery" in html and "500.00" in html
        assert email.attachments and email.attachments[0][0] == f"Invoice-INV-{order.order_number}.pdf"

    def test_gstin_appears_in_the_email_invoice_only_when_configured(self, api, commit, settings):
        settings.SELLER = {**settings.SELLER, "gstin": "22AAAAA0000A1Z5"}
        _, email = self.sent(api, commit)
        assert "22AAAAA0000A1Z5" in email.alternatives[0][0] and "22AAAAA0000A1Z5" in email.body


class TestEmailsSwitchedOff:
    """NOTIFICATIONS_ENABLED=false: the whole flow works, nothing is emailed, and staff are told why."""

    @pytest.fixture
    def staff_client(self, client):
        client.force_login(
            User.objects.create_superuser(
                email="root@example.com", name="Root", mobile="9000000001", password="s3cret-pass-1"
            )
        )
        return client

    def test_the_order_lifecycle_sends_no_email(self, api, commit, settings):
        settings.NOTIFICATIONS_ENABLED = False
        product = make_product(price=500, stock_quantity=5)
        with commit():
            order = Order.objects.get(pk=place(api, (product, 1)).data["order"]["id"])
            checkout.confirm_order(order.pk)
            checkout.mark_shipped(order.pk, courier_name="Delhivery", tracking_number="DL1")
            checkout.mark_delivered(order.pk)
        order.refresh_from_db()
        assert order.status == "delivered"  # the business flow is untouched
        assert mail.outbox == [] and EmailLog.objects.count() == 0

    def test_admin_actions_say_emails_are_off_instead_of_failing(self, staff_client, api, commit, settings):
        settings.NOTIFICATIONS_ENABLED = False
        with commit():
            order_id = place(api, (make_product(price=500), 1)).data["order"]["id"]
        response = staff_client.post(
            reverse("admin:orders_order_email_invoice", args=[order_id]), follow=True
        )
        messages = [str(m) for m in response.context["messages"]]
        assert any("turned off" in m for m in messages) and not any(
            "could not be sent" in m for m in messages
        )
        assert mail.outbox == []
