import io

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify
from PIL import Image

from apps.catalog import seed_data as data
from apps.catalog.models import Category, Product, ProductMedia, ProductVariant, VariantOption

DESCRIPTION = (
    "{summary}\n\nEvery unit is tested before dispatch and ships with wiring diagrams and example code.\n\n"
    "Covered by a 12-month replacement warranty."
)
PALETTE = ["#1d4ed8", "#0f766e", "#b45309", "#7c3aed", "#be123c", "#334155"]


def _placeholder(color: str, size=(800, 600)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def _round_price(value: float) -> int:
    return round(value / 10) * 10 - 1


class Command(BaseCommand):
    help = "Create (or refresh) sample categories, products, images and variants. Safe to re-run."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Allow running with DEBUG off.")

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError("Sample data is for development; pass --force to run with DEBUG off.")

        categories: dict[str, Category] = {}
        for order, (slug, (name, description, children)) in enumerate(data.CATEGORIES.items()):
            top, _ = Category.objects.update_or_create(
                slug=slug,
                defaults={"name": name, "description": description, "sort_order": order, "parent": None},
            )
            categories[slug] = top
            if not top.image:
                top.image.save(f"{slug}.png", ContentFile(_placeholder(PALETTE[order % len(PALETTE)])))
            for child_order, (child_slug, child_name) in enumerate(children):
                categories[child_slug], _ = Category.objects.update_or_create(
                    slug=child_slug, defaults={"name": child_name, "parent": top, "sort_order": child_order}
                )

        for index, (
            slug,
            name,
            summary,
            cat,
            price,
            compare,
            rating,
            reviews,
            is_new,
            sold,
            in_stock,
        ) in enumerate(data.PRODUCTS):
            category = categories[cat]
            product, _ = Product.objects.update_or_create(
                slug=slug,
                defaults={
                    "name": name,
                    "summary": summary,
                    "category": category,
                    "price": price,
                    "compare_at_price": compare,
                    "rating": rating,
                    "review_count": reviews,
                    "is_new": is_new,
                    "sold_count": sold,
                    "stock_quantity": 50 if in_stock else 0,
                    "description": DESCRIPTION.format(summary=summary),
                },
            )
            self._images(product, index)
            self._variants(product, category.top_level.slug, price, compare)

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {Category.objects.count()} categories and {Product.objects.count()} products."
            )
        )

    def _images(self, product: Product, index: int):
        if product.media.exists():
            return
        for position in range(2 + index % 4):  # 2-5 images so every gallery layout is exercised
            ProductMedia.objects.create(
                product=product,
                position=position,
                alt=f"{product.name}, view {position + 1}",
                file=ContentFile(
                    _placeholder(PALETTE[(index + position) % len(PALETTE)]),
                    name=f"{product.slug}-{position + 1}.png",
                ),
            )

    def _variants(self, product: Product, top_slug: str, price, compare):
        spec = data.VARIANTS.get(top_slug)
        if not spec:
            return
        variant_type, options = spec
        Product.objects.filter(pk=product.pk).update(variant_label=VariantOption.Type(variant_type).label)
        seeded = []
        for position, (title, factor, in_stock) in enumerate(options):
            option, _ = VariantOption.objects.get_or_create(
                type=variant_type, slug=slugify(title), defaults={"title": title}
            )
            seeded.append(option.pk)
            ProductVariant.objects.update_or_create(
                product=product,
                option=option,
                defaults={
                    "price": price if position == 0 else _round_price(price * factor),
                    "compare_at_price": None
                    if compare is None
                    else (compare if position == 0 else _round_price(compare * factor)),
                    "in_stock": in_stock,
                    "image_index": position,
                    "position": position,
                },
            )
        # Seeding is a refresh: drop variants left over from an earlier layout of the sample data.
        product.variants.exclude(option_id__in=seeded).delete()
