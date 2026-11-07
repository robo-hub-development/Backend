"""0003: free-text article categories become the category master and image becomes media, keeping data."""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

BEFORE = [("content", "0002_article")]
AFTER = "0003_article_media_and_category_master"


def migrate_to(targets):
    executor = MigrationExecutor(connection)
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


def migrate_latest():
    executor = MigrationExecutor(connection)
    leaves = executor.loader.graph.leaf_nodes()
    executor.migrate(leaves)
    return executor.loader.project_state(leaves).apps


def make_old(apps, slug, category, image="articles/a.png"):
    Article = apps.get_model("content", "Article")
    return Article.objects.create(
        title=slug.replace("-", " "),
        slug=slug,
        body="<p>Text</p>",
        image=image,
        category=category,
        is_published=True,
        published_at=timezone.now(),
    )


@pytest.mark.django_db(transaction=True)
class TestCategoryMasterMigration:
    def teardown_method(self):
        migrate_latest()  # leave the test database fully migrated for the tests that follow

    def test_text_categories_become_master_rows_and_articles_keep_their_files(self):
        old = migrate_to(BEFORE)
        make_old(old, "one", "Guides", image="articles/one.png")
        make_old(old, "two", "guides ")  # same category: different case, stray space
        make_old(old, "three", "Tutorials")
        make_old(old, "four", "")  # no category
        make_old(old, "five", "C++ & Robots")
        make_old(old, "six", "C & Robots")  # slugs to the same value as the one above

        new = migrate_latest()
        Article, Category = new.get_model("content", "Article"), new.get_model("content", "ArticleCategory")
        articles = {a.slug: a for a in Article.objects.select_related("category")}

        assert sorted(Category.objects.values_list("name", flat=True)) == sorted(
            ["Guides", "Tutorials", "C++ & Robots", "C & Robots"]
        )
        assert articles["one"].category_id == articles["two"].category_id  # merged, not duplicated
        assert articles["one"].category.name == "Guides" and articles["three"].category.name == "Tutorials"
        assert articles["four"].category is None
        slugs = list(Category.objects.values_list("slug", flat=True))
        assert len(slugs) == len(set(slugs)) == 4 and all(slugs)
        # image -> media: same file, now typed
        assert articles["one"].media.name == "articles/one.png" and articles["one"].media_type == "image"

    def test_it_can_be_reversed_without_losing_the_category_names(self):
        old = migrate_to(BEFORE)
        make_old(old, "one", "Guides")
        make_old(old, "two", "Tutorials")
        migrate_latest()

        back = migrate_to(BEFORE)
        Article = back.get_model("content", "Article")
        assert dict(Article.objects.values_list("slug", "category")) == {"one": "Guides", "two": "Tutorials"}
        assert Article.objects.get(slug="one").image == "articles/a.png"

    def test_an_empty_table_migrates_cleanly(self):
        migrate_to(BEFORE)
        new = migrate_latest()
        assert new.get_model("content", "ArticleCategory").objects.count() == 0
