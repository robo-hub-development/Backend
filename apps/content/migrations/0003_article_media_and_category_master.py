import uuid

import django.db.models.deletion
import django.db.models.functions.text
from django.conf import settings
from django.db import migrations, models
from django.utils.text import slugify

import apps.catalog.validators


def text_to_master(apps, schema_editor):
    """Turn the free-text categories into master rows (one per distinct name, case-insensitive) and link them."""
    Article = apps.get_model("content", "Article")
    ArticleCategory = apps.get_model("content", "ArticleCategory")
    by_name: dict[str, object] = {}
    used_slugs: set[str] = set()
    for article in Article.objects.exclude(category_text="").order_by("created_at"):
        name = article.category_text.strip()
        key = name.lower()
        if key not in by_name:
            base = slugify(name)[:70] or "category"
            slug, n = base, 2
            while slug in used_slugs or ArticleCategory.objects.filter(slug=slug).exists():
                slug, n = f"{base}-{n}", n + 1
            used_slugs.add(slug)
            by_name[key] = ArticleCategory.objects.create(name=name[:60], slug=slug, sort_order=len(by_name))
        Article.objects.filter(pk=article.pk).update(category_ref=by_name[key])


def master_to_text(apps, schema_editor):
    Article = apps.get_model("content", "Article")
    for article in Article.objects.exclude(category_ref=None).select_related("category_ref"):
        Article.objects.filter(pk=article.pk).update(category_text=article.category_ref.name)


class Migration(migrations.Migration):
    dependencies = [
        ("content", "0002_article"),
        ("catalog", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ArticleCategory",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=60)),
                (
                    "slug",
                    models.SlugField(
                        help_text="Used to filter the blog: /blog?category=<slug>.", max_length=80, unique=True
                    ),
                ),
                ("sort_order", models.PositiveSmallIntegerField(default=0, help_text="Lowest number is shown first.")),
                (
                    "is_active",
                    models.BooleanField(default=True, help_text="Inactive categories are hidden from the category list."),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "modified_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name_plural": "article categories",
                "ordering": ["sort_order", "name"],
            },
        ),
        migrations.AddConstraint(
            model_name="articlecategory",
            constraint=models.UniqueConstraint(
                django.db.models.functions.text.Lower("name"), name="articlecategory_name_ci_unique"
            ),
        ),
        # image -> media: any image or video, type detected from the file (existing files stay where they are).
        migrations.RenameField(model_name="article", old_name="image", new_name="media"),
        migrations.AlterField(
            model_name="article",
            name="media",
            field=models.FileField(
                help_text="The main image or video (jpg, png, webp, gif, mp4, webm); its type is detected.",
                upload_to="articles/",
                validators=[apps.catalog.validators.validate_media_file],
            ),
        ),
        migrations.AddField(
            model_name="article",
            name="media_type",
            field=models.CharField(
                choices=[("image", "Image"), ("video", "Video")], default="image", editable=False, max_length=5
            ),
        ),
        # category: free text -> master table, converted in place.
        migrations.RenameField(model_name="article", old_name="category", new_name="category_text"),
        migrations.AddField(
            model_name="article",
            name="category_ref",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="articles",
                to="content.articlecategory",
            ),
        ),
        migrations.RunPython(text_to_master, master_to_text),
        migrations.RemoveField(model_name="article", name="category_text"),
        migrations.RenameField(model_name="article", old_name="category_ref", new_name="category"),
        migrations.AlterField(
            model_name="article",
            name="category",
            field=models.ForeignKey(
                blank=True,
                help_text="Pick from the article categories (manage them under Article categories).",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="articles",
                to="content.articlecategory",
            ),
        ),
    ]
