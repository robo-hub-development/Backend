import django.db.models.deletion
from django.db import migrations, models

import apps.catalog.validators


class Migration(migrations.Migration):
    """ProductImage (images only) -> ProductMedia (images + videos). Existing rows are kept as images."""

    dependencies = [("catalog", "0001_initial")]

    operations = [
        migrations.RenameModel(old_name="ProductImage", new_name="ProductMedia"),
        migrations.RenameField(model_name="productmedia", old_name="image", new_name="file"),
        migrations.AlterField(
            model_name="productmedia",
            name="file",
            field=models.FileField(upload_to="products/", validators=[apps.catalog.validators.validate_media_file]),
        ),
        migrations.AddField(
            model_name="productmedia",
            name="media_type",
            field=models.CharField(
                choices=[("image", "Image"), ("video", "Video")], default="image", editable=False, max_length=5
            ),
        ),
        migrations.AlterField(
            model_name="productmedia",
            name="product",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, related_name="media", to="catalog.product"
            ),
        ),
        migrations.AlterModelOptions(
            name="productmedia", options={"ordering": ["position", "created_at"], "verbose_name_plural": "product media"}
        ),
        migrations.AlterField(
            model_name="productmedia",
            name="alt",
            field=models.CharField(blank=True, help_text="Description / title shown with the media.", max_length=200),
        ),
        migrations.AlterField(
            model_name="productmedia",
            name="position",
            field=models.PositiveSmallIntegerField(
                default=0, help_text="Lowest position of an image is the main image."
            ),
        ),
        migrations.AlterField(
            model_name="productvariant",
            name="image_index",
            field=models.PositiveSmallIntegerField(
                default=0, help_text="Index into the product's images (photos only, videos excluded)."
            ),
        ),
    ]
