import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.tests.conftest import make_product
from apps.orders.models import Order

from .conftest import client_for
from .test_orders_api import URL, place

pytestmark = pytest.mark.django_db


def _post_data(response, order_id, **fields):
    """What the browser posts: the editable fields plus the inline formsets' hidden management/id fields."""
    data = dict(fields)
    for inline in response.context["inline_admin_formsets"]:
        formset = inline.formset
        prefix = formset.prefix
        data[f"{prefix}-TOTAL_FORMS"] = len(formset.forms)
        data[f"{prefix}-INITIAL_FORMS"] = len(formset.forms)
        data[f"{prefix}-MIN_NUM_FORMS"] = 0
        data[f"{prefix}-MAX_NUM_FORMS"] = 1000
        for index, form in enumerate(formset.forms):
            data[f"{prefix}-{index}-id"] = str(form.instance.pk)
            data[f"{prefix}-{index}-{formset.fk.name}"] = str(order_id)
    return data


def test_order_has_no_tracking_until_staff_enter_it(api, member):
    placed = place(api, (make_product(), 1)).data["order"]
    assert placed["courierName"] == ""
    assert placed["trackingNumber"] == ""


def test_customer_sees_the_tracking_number_on_the_order(api, member):
    order_id = place(api, (make_product(), 1)).data["order"]["id"]
    Order.objects.filter(pk=order_id).update(courier_name="Delhivery", tracking_number="DL123456789")

    detail = api.get(f"{URL}/{order_id}").data
    assert (detail["courierName"], detail["trackingNumber"]) == ("Delhivery", "DL123456789")
    assert api.get(URL).data["items"][0]["trackingNumber"] == "DL123456789"


def test_tracking_number_is_not_visible_to_another_customer(api, member, other_member):
    order_id = place(api, (make_product(), 1)).data["order"]["id"]
    Order.objects.filter(pk=order_id).update(tracking_number="DL123456789")
    assert client_for(other_member).get(f"{URL}/{order_id}").status_code == 404


def test_staff_set_only_the_tracking_fields_in_admin(client, api, member):
    order_id = place(api, (make_product(), 1)).data["order"]["id"]
    before = Order.objects.get(pk=order_id)
    staff = User.objects.create_superuser(
        email="root@example.com", name="Root", mobile="9000000001", password="s3cret-pass-1"
    )
    client.force_login(staff)
    url = reverse("admin:orders_order_change", args=[order_id])

    form = _post_data(
        client.get(url), order_id, courier_name=" Delhivery ", tracking_number="DL123456789", total="1"
    )
    assert client.post(url, form).status_code == 302

    order = Order.objects.get(pk=order_id)
    assert (order.courier_name, order.tracking_number) == ("Delhivery", "DL123456789")
    assert order.modified_by == staff
    # Money and status are not editable through this form, even if the request tries to send them.
    assert (order.total, order.status) == (before.total, before.status)
