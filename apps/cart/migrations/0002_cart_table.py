import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def build_carts(apps, schema_editor):
    """Give every user that already has lines a Cart and attach the lines to it."""
    Cart = apps.get_model("cart", "Cart")
    CartItem = apps.get_model("cart", "CartItem")
    for user_id in CartItem.objects.order_by().values_list("user_id", flat=True).distinct():
        cart = Cart.objects.create(user_id=user_id)
        CartItem.objects.filter(user_id=user_id).update(cart=cart)


def restore_users(apps, schema_editor):
    CartItem = apps.get_model("cart", "CartItem")
    for item in CartItem.objects.select_related("cart"):
        item.user_id = item.cart.user_id
        item.save(update_fields=["user"])


class Migration(migrations.Migration):
    dependencies = [
        ("cart", "0001_initial"),
        ("catalog", "0003_variantoption"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Cart",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="cart",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"abstract": False},
        ),
        migrations.AddField(
            model_name="cartitem",
            name="cart",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="items",
                to="cart.cart",
            ),
        ),
        migrations.AddField(
            model_name="cartitem",
            name="ordered_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        # user is nullable for the duration of the move so the migration can also be reversed.
        migrations.AlterField(
            model_name="cartitem",
            name="user",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="cart_items",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(build_carts, restore_users),
        migrations.RemoveConstraint(model_name="cartitem", name="cartitem_unique_product_per_user"),
        migrations.RemoveField(model_name="cartitem", name="user"),
        migrations.AlterField(
            model_name="cartitem",
            name="cart",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, related_name="items", to="cart.cart"
            ),
        ),
        migrations.AddConstraint(
            model_name="cartitem",
            constraint=models.UniqueConstraint(
                condition=models.Q(("ordered_at__isnull", True)),
                fields=("cart", "product"),
                name="cartitem_unique_live_product_per_cart",
            ),
        ),
    ]
