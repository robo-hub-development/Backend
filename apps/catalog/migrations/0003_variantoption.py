import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils.text import slugify


def label_to_option(apps, schema_editor):
    """Existing free-text variant labels become master rows (type Size by default; staff can re-type them)."""
    VariantOption = apps.get_model("catalog", "VariantOption")
    ProductVariant = apps.get_model("catalog", "ProductVariant")
    for variant in ProductVariant.objects.all():
        slug = slugify(variant.label)[:120] or "option"
        option, _ = VariantOption.objects.get_or_create(
            type="size", slug=slug, defaults={"title": variant.label[:100]}
        )
        variant.option = option
        variant.save(update_fields=["option"])


class Migration(migrations.Migration):
    """Introduce the VariantOption master table and map ProductVariant to it (replaces the free-text label)."""

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("catalog", "0002_productmedia"),
    ]

    operations = [
        migrations.CreateModel(
            name="VariantOption",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("title", models.CharField(max_length=100)),
                ("slug", models.SlugField(max_length=120)),
                ("type", models.CharField(choices=[("size", "Size"), ("color", "Color")], max_length=10)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+", to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "modified_by",
                    models.ForeignKey(
                        blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+", to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["type", "title"]},
        ),
        migrations.AddConstraint(
            model_name="variantoption",
            constraint=models.UniqueConstraint(fields=("type", "slug"), name="variant_option_unique_slug_per_type"),
        ),
        migrations.AddConstraint(
            model_name="variantoption",
            constraint=models.UniqueConstraint(fields=("type", "title"), name="variant_option_unique_title_per_type"),
        ),
        migrations.AddField(
            model_name="productvariant",
            name="option",
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.PROTECT, related_name="product_variants",
                to="catalog.variantoption",
            ),
        ),
        migrations.RunPython(label_to_option, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="productvariant",
            name="option",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT, related_name="product_variants",
                to="catalog.variantoption",
            ),
        ),
        migrations.RemoveConstraint(model_name="productvariant", name="variant_unique_label_per_product"),
        migrations.RemoveField(model_name="productvariant", name="label"),
        migrations.AddConstraint(
            model_name="productvariant",
            constraint=models.UniqueConstraint(fields=("product", "option"), name="variant_unique_option_per_product"),
        ),
    ]
