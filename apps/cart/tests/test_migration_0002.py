import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

BEFORE = [("cart", "0001_initial"), ("orders", "0001_initial")]


@pytest.mark.django_db(transaction=True)
def test_existing_lines_are_moved_into_one_cart_per_user():
    executor = MigrationExecutor(connection)
    executor.migrate(BEFORE)
    old = executor.loader.project_state(BEFORE).apps
    User, Category = old.get_model("accounts", "User"), old.get_model("catalog", "Category")
    Product, Line = old.get_model("catalog", "Product"), old.get_model("cart", "CartItem")

    category = Category.objects.create(name="C", slug="c")
    products = [
        Product.objects.create(name=f"P{i}", slug=f"p{i}", category=category, price=10) for i in range(2)
    ]
    ana = User.objects.create(email="ana@example.com", name="Ana", mobile="9000000011")
    ben = User.objects.create(email="ben@example.com", name="Ben", mobile="9000000012")
    Line.objects.create(user=ana, product=products[0], quantity=2)
    Line.objects.create(user=ana, product=products[1], quantity=1)
    Line.objects.create(user=ben, product=products[0], quantity=3)

    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())
    new = executor.loader.project_state(executor.loader.graph.leaf_nodes()).apps
    Cart, NewLine = new.get_model("cart", "Cart"), new.get_model("cart", "CartItem")

    assert Cart.objects.count() == 2
    assert NewLine.objects.filter(cart__user__email="ana@example.com").count() == 2
    assert NewLine.objects.get(cart__user__email="ben@example.com").quantity == 3
    assert not NewLine.objects.filter(cart__isnull=True).exists()
