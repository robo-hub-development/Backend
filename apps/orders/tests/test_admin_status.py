import pytest
from django.contrib.messages import get_messages
from django.core import mail
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Product
from apps.catalog.tests.conftest import make_product
from apps.notifications.models import EmailLog
from apps.orders.models import CodTransitPolicy, Order, OrderStatusEvent
from apps.payments.tests.helpers import online_order, pay

from .test_orders_api import place

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff(db):
    return User.objects.create_superuser(
        email="root@example.com", name="Root", mobile="9000000001", password="s3cret-pass-1"
    )


@pytest.fixture
def staff_client(client, staff):
    client.force_login(staff)
    return client


@pytest.fixture
def commit(django_capture_on_commit_callbacks):
    return lambda: django_capture_on_commit_callbacks(execute=True)


def status_url(order):
    return reverse("admin:orders_order_status", args=[order.pk])


def act(client, order, action, **data):
    return client.post(status_url(order), {"action": action, **data}, follow=True)


def texts(response):
    return [str(m) for m in get_messages(response.wsgi_request)]


def cod_order(api, price=500, name="ESP32 DevKit"):
    product = make_product(name, price=price, stock_quantity=5)
    return product, Order.objects.get(pk=place(api, (product, 1)).data["order"]["id"])


class TestWorkflow:
    def test_cod_order_goes_from_pending_to_delivered_and_paid(self, staff_client, api, staff, commit):
        _, order = cod_order(api)
        assert order.status == "pending"

        with commit():
            assert act(staff_client, order, "confirm").status_code == 200
        order.refresh_from_db()
        assert order.status == "confirmed"

        with commit():
            act(staff_client, order, "ship", courier_name=" Delhivery ", tracking_number="DL123456")
        order.refresh_from_db()
        assert (order.status, order.courier_name, order.tracking_number) == (
            "shipped",
            "Delhivery",
            "DL123456",
        )

        with commit():
            act(staff_client, order, "deliver")
        order.refresh_from_db()
        assert (order.status, order.payment_status, order.amount_paid) == ("delivered", "paid", order.total)

        history = list(OrderStatusEvent.objects.filter(order=order).values_list("to_status", "actor"))
        assert history[-3:] == [("confirmed", staff.pk), ("shipped", staff.pk), ("delivered", staff.pk)]

    def test_shipping_emails_the_invoice_with_the_tracking_details(self, staff_client, api, commit):
        _, order = cod_order(api)
        with commit():
            act(staff_client, order, "confirm")
        mail.outbox.clear()
        with commit():
            act(staff_client, order, "ship", courier_name="Delhivery", tracking_number="DL999")
        assert sorted(EmailLog.objects.values_list("kind", flat=True)) == ["invoice", "order_shipped"]
        email = next(m for m in mail.outbox if m.attachments)
        assert "invoice" in email.subject.lower() and "DL999" in email.alternatives[0][0]
        from apps.notifications.tests.test_notifications import pdf_text

        assert email.attachments[0][2] == "application/pdf" and "DL999" in pdf_text(email.attachments[0][1])
        assert EmailLog.objects.filter(kind="invoice", status="sent").count() == 1

    def test_shipping_works_without_tracking_details(self, staff_client, api, commit):
        _, order = cod_order(api)
        with commit():
            act(staff_client, order, "confirm")
            act(staff_client, order, "ship")
        order.refresh_from_db()
        assert order.status == "shipped" and order.tracking_number == ""

    def test_online_order_can_be_shipped_and_delivered(self, staff_client, api, commit):
        _, order, payment = online_order(api, price=250)
        with commit():
            pay(payment)
        order.refresh_from_db()
        assert order.status == "confirmed"
        with commit():
            act(staff_client, order, "ship")
            act(staff_client, order, "deliver")
        order.refresh_from_db()
        assert (order.status, order.payment_status) == ("delivered", "paid")

    def test_return_to_origin_keeps_the_fee_and_drops_the_cash(self, staff_client, api, commit):
        CodTransitPolicy.objects.create(scope="default", mode="fixed", value=40)
        _, order = cod_order(api)
        with commit():
            pay(order.payments.get(purpose="cod_fee"))
            act(staff_client, order, "confirm")
            act(staff_client, order, "ship")
            act(staff_client, order, "rto")
        order.refresh_from_db()
        assert order.status == "rto" and order.amount_paid == 40
        assert order.payments.get(purpose="cod_balance").status == "uncollectable"
        assert order.payments.get(purpose="cod_fee").status == "captured"  # kept for the transit


class TestStaffCancel:
    def test_cancel_restores_stock_and_records_who_and_why(self, staff_client, api, staff):
        product, order = cod_order(api)
        assert Product.objects.get(pk=product.pk).stock_quantity == 4
        act(staff_client, order, "cancel", note="Customer called")
        order.refresh_from_db()
        assert order.status == "cancelled" and Product.objects.get(pk=product.pk).stock_quantity == 5
        event = OrderStatusEvent.objects.filter(order=order).last()
        assert (event.to_status, event.actor_id, event.reason) == (
            "cancelled",
            staff.pk,
            "cancelled by staff: Customer called",
        )

    def test_cancelling_a_paid_online_order_starts_the_refund(self, staff_client, api, commit):
        _, order, payment = online_order(api, price=250)
        with commit():
            pay(payment, payment_id="pay_77")
        mail.outbox.clear()
        with commit():
            act(staff_client, order, "cancel")
        order.refresh_from_db()
        assert (order.status, order.payment_status) == ("cancelled", "refund_in_progress")
        assert len(mail.outbox) == 1 and "refund" in mail.outbox[0].subject.lower()

    def test_staff_can_cancel_after_the_customers_cancellation_window(self, staff_client, api, settings):
        settings.ORDER_CANCEL_WINDOW_HOURS = 0
        _, order = cod_order(api)
        assert not Order.objects.get(pk=order.pk).can_cancel  # the customer no longer can
        act(staff_client, order, "cancel")
        order.refresh_from_db()
        assert order.status == "cancelled"

    def test_an_unpaid_order_can_be_cancelled_and_releases_its_stock(self, staff_client, api):
        product, order, _ = online_order(api, price=250, stock=5)
        act(staff_client, order, "cancel")
        order.refresh_from_db()
        assert order.status == "cancelled" and Product.objects.get(pk=product.pk).stock_quantity == 5

    def test_shipped_orders_cannot_be_cancelled(self, staff_client, api, commit):
        _, order = cod_order(api)
        with commit():
            act(staff_client, order, "confirm")
            act(staff_client, order, "ship")
        response = act(staff_client, order, "cancel")
        order.refresh_from_db()
        assert order.status == "shipped"
        assert any("not possible while the order is Shipped" in t for t in texts(response))


class TestGuards:
    def test_impossible_steps_change_nothing_and_say_why(self, staff_client, api):
        _, order = cod_order(api)  # pending
        for action in ("ship", "deliver", "rto"):
            response = act(staff_client, order, action)
            assert any("not possible while the order is Pending" in t for t in texts(response)), action
        order.refresh_from_db()
        assert order.status == "pending"

    def test_finished_orders_offer_no_actions(self, staff_client, api):
        _, order = cod_order(api)
        act(staff_client, order, "cancel")
        page = staff_client.get(reverse("admin:orders_order_change", args=[order.pk])).content.decode()
        assert "No further status changes are possible" in page and 'name="action"' not in page

    def test_the_panel_offers_only_the_valid_steps(self, staff_client, api):
        _, order = cod_order(api)  # pending: accept or cancel
        page = staff_client.get(reverse("admin:orders_order_change", args=[order.pk])).content.decode()
        assert 'value="confirm"' in page and 'value="cancel"' in page
        assert 'value="ship"' not in page and 'value="deliver"' not in page

    def test_unknown_action_and_unknown_order(self, staff_client, api):
        _, order = cod_order(api)
        assert staff_client.post(status_url(order), {"action": "explode"}).status_code == 404
        ghost = reverse("admin:orders_order_status", args=["00000000-0000-0000-0000-000000000000"])
        assert staff_client.post(ghost, {"action": "confirm"}).status_code == 404

    def test_a_get_request_changes_nothing(self, staff_client, api):
        _, order = cod_order(api)
        assert staff_client.get(status_url(order) + "?action=confirm").status_code == 405
        order.refresh_from_db()
        assert order.status == "pending"

    def test_the_status_cannot_be_forged_through_the_normal_save_form(self, staff_client, api):
        _, order = cod_order(api)
        url = reverse("admin:orders_order_change", args=[order.pk])
        page = staff_client.get(url)
        assert "status" not in page.context["adminform"].form.fields  # read-only: no field to tamper with

    def test_staff_without_change_permission_cannot_update_status(self, client, api):
        _, order = cod_order(api)
        viewer = User.objects.create_user(email="v@example.com", name="V", mobile="9000000002", is_staff=True)
        client.force_login(viewer)
        assert client.post(status_url(order), {"action": "confirm"}).status_code == 404
        order.refresh_from_db()
        assert order.status == "pending"

    def test_anonymous_users_are_sent_to_login(self, client, api):
        _, order = cod_order(api)
        assert client.post(status_url(order), {"action": "confirm"}).status_code == 302


class TestBulkActions:
    def post_action(self, client, action, orders):
        return client.post(
            reverse("admin:orders_order_changelist"),
            {"action": action, "_selected_action": [str(o.pk) for o in orders]},
            follow=True,
        )

    def test_accept_ship_and_deliver_several_orders(self, staff_client, api, commit):
        orders = [cod_order(api, name=f"Board {n}")[1] for n in range(3)]
        with commit():
            self.post_action(staff_client, "accept_orders", orders)
            self.post_action(staff_client, "ship_orders", orders)
            self.post_action(staff_client, "deliver_orders", orders)
        assert set(Order.objects.values_list("status", flat=True)) == {"delivered"}

    def test_a_bulk_run_skips_orders_in_the_wrong_state_and_reports_them(self, staff_client, api):
        pending = cod_order(api, name="A")[1]
        done = cod_order(api, name="B")[1]
        act(staff_client, done, "cancel")
        response = self.post_action(staff_client, "accept_orders", [pending, done])
        pending.refresh_from_db()
        done.refresh_from_db()
        assert (pending.status, done.status) == ("confirmed", "cancelled")
        assert any(done.order_number in t and "not possible" in t for t in texts(response))
        assert any("1 order(s) updated" in t for t in texts(response))
