from datetime import timedelta
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.text import slugify
from PIL import Image

from apps.content.models import Article, ArticleCategory
from apps.content.seed_data import SAMPLES


def _placeholder(color: str, size=(1280, 720)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


class Command(BaseCommand):
    help = "Create three sample blog articles (idempotent by slug) for local development."

    def handle(self, *args, **options):
        created = 0
        for sample in SAMPLES:
            data = {k: v for k, v in sample.items() if k not in ("color", "days_ago", "category")}
            category, _ = ArticleCategory.objects.get_or_create(
                slug=slugify(sample["category"]), defaults={"name": sample["category"]}
            )
            article, new = Article.objects.get_or_create(
                slug=sample["slug"],
                defaults={
                    **data,
                    "category": category,
                    "is_published": True,
                    "published_at": timezone.now() - timedelta(days=sample["days_ago"]),
                },
            )
            if new:
                article.media.save(
                    f"{sample['slug']}.png", ContentFile(_placeholder(sample["color"])), save=True
                )
                created += 1
        self.stdout.write(
            self.style.SUCCESS(f"{created} article(s) created, {len(SAMPLES) - created} already existed.")
        )
