from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.catalog.tests.conftest import MP4
from apps.common.richtext import plain_text
from apps.content.models import Article, ArticleCategory
from apps.content.tests.test_banners import GIF

LIST = "/api/v1/articles"
BODY = "<p>Starting out in robotics is easier when the kit matches what you want to learn.</p><h2>Goal</h2>"


@pytest.fixture(autouse=True)
def _media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


def make_category(name="Guides", **kw):
    kw.setdefault("slug", name.lower().replace(" ", "-"))
    return ArticleCategory.objects.create(name=name, **kw)


def make_article(title="Choosing your first robot kit", **kw):
    kw.setdefault("slug", title.lower().replace(" ", "-"))
    kw.setdefault("body", BODY)
    kw.setdefault("media", SimpleUploadedFile("a.png", GIF, content_type="image/png"))
    kw.setdefault("is_published", True)
    kw.setdefault("published_at", timezone.now() - timedelta(hours=1))
    return Article.objects.create(title=title, **kw)


@pytest.mark.django_db
class TestArticleApi:
    def test_list_shape_matches_the_storefront_contract(self):
        article = make_article(
            excerpt="Motors, controllers and sensors.", category=make_category("Guides"), author="Team"
        )
        res = APIClient().get(LIST)
        assert res.status_code == 200
        (item,) = res.data
        assert set(item) == {
            "slug",
            "title",
            "excerpt",
            "image",
            "media",
            "mediaType",
            "category",
            "categorySlug",
            "author",
            "publishedAt",
            "readMinutes",
        }
        assert item["slug"] == article.slug and item["title"] == article.title
        assert item["excerpt"] == "Motors, controllers and sensors."
        assert (item["category"], item["categorySlug"], item["author"], item["readMinutes"]) == (
            "Guides",
            "guides",
            "Team",
            1,
        )
        assert item["image"].startswith("http") and item["image"].endswith(".png")
        assert item["media"] == item["image"] and item["mediaType"] == "image"
        assert item["publishedAt"].startswith(article.published_at.strftime("%Y-%m-%d"))
        assert "content" not in item  # the body is only on the detail endpoint

    def test_the_response_uses_the_standard_envelope(self):
        make_article()
        body = APIClient().get(LIST).json()
        assert body["success"] is True and body["error"] is None and isinstance(body["data"], list)

    def test_newest_first(self):
        now = timezone.now()
        make_article("Old", published_at=now - timedelta(days=5))
        make_article("Newest", published_at=now - timedelta(hours=1))
        make_article("Middle", published_at=now - timedelta(days=2))
        assert [a["title"] for a in APIClient().get(LIST).data] == ["Newest", "Middle", "Old"]

    def test_drafts_and_scheduled_articles_are_hidden(self):
        make_article("Live")
        make_article("Draft", is_published=False)
        make_article("Later", published_at=timezone.now() + timedelta(days=1))
        assert [a["title"] for a in APIClient().get(LIST).data] == ["Live"]

    def test_limit(self):
        for n in range(5):
            make_article(f"Post {n}", published_at=timezone.now() - timedelta(hours=n + 1))
        assert [a["title"] for a in APIClient().get(f"{LIST}?limit=2").data] == ["Post 0", "Post 1"]
        assert len(APIClient().get(LIST).data) == 5

    @pytest.mark.parametrize("limit", ["0", "101", "abc", "-1"])
    def test_a_bad_limit_is_rejected(self, limit):
        res = APIClient().get(f"{LIST}?limit={limit}")
        assert res.status_code == 400 and "limit" in res.data["errors"]

    def test_empty_list(self):
        res = APIClient().get(LIST)
        assert res.status_code == 200 and res.data == []

    def test_list_does_not_query_per_article(self, django_assert_max_num_queries):
        for n in range(8):
            make_article(f"Post {n}", published_at=timezone.now() - timedelta(hours=n + 1))
        with django_assert_max_num_queries(2):
            assert len(APIClient().get(LIST).data) == 8

    def test_detail_includes_the_html_content(self):
        article = make_article(author="Aarav Mehta", category=make_category("Tutorials"))
        res = APIClient().get(f"{LIST}/{article.slug}")
        assert res.status_code == 200
        assert res.data["content"] == article.body and "<h2>Goal</h2>" in res.data["content"]
        assert res.data["slug"] == article.slug and res.data["author"] == "Aarav Mehta"
        assert set(res.data) == {
            "slug",
            "title",
            "excerpt",
            "image",
            "media",
            "mediaType",
            "category",
            "categorySlug",
            "author",
            "publishedAt",
            "readMinutes",
            "content",
        }

    @pytest.mark.parametrize(
        "kwargs",
        [{"is_published": False}, {"published_at": timezone.now() + timedelta(days=1)}],
        ids=["draft", "scheduled"],
    )
    def test_unpublished_detail_is_a_404_just_like_an_unknown_slug(self, kwargs):
        article = make_article(**kwargs)
        hidden = APIClient().get(f"{LIST}/{article.slug}")
        unknown = APIClient().get(f"{LIST}/no-such-article")
        assert hidden.status_code == unknown.status_code == 404
        assert hidden.json() == unknown.json()  # nothing reveals that the draft exists

    def test_a_scheduled_article_appears_once_its_time_has_come(self):
        article = make_article(published_at=timezone.now() + timedelta(hours=1))
        assert APIClient().get(f"{LIST}/{article.slug}").status_code == 404
        Article.objects.filter(pk=article.pk).update(published_at=timezone.now() - timedelta(seconds=1))
        assert APIClient().get(f"{LIST}/{article.slug}").status_code == 200

    def test_the_api_is_public_and_read_only(self):
        article = make_article()
        client = APIClient()
        assert client.get(LIST).status_code == 200
        for method in (client.post, client.put, client.patch, client.delete):
            assert method(LIST, {}).status_code == 405
            assert method(f"{LIST}/{article.slug}", {}).status_code == 405


@pytest.mark.django_db
class TestArticleModel:
    def test_the_body_is_cleaned_on_save(self):
        article = make_article(
            body='<p onclick="x()">Hi</p><script>alert(1)</script><a href="javascript:x">bad</a>'
        )
        assert (
            "<script" not in article.body
            and "onclick" not in article.body
            and "javascript:" not in article.body
        )
        assert "Hi" in article.body

    def test_reading_time_follows_the_word_count(self):
        assert make_article("Short", body="<p>" + "word " * 50 + "</p>").read_minutes == 1
        assert make_article("Medium", body="<p>" + "word " * 450 + "</p>").read_minutes == 3
        assert make_article("Empty", body="").read_minutes == 1  # never zero

    def test_an_empty_excerpt_is_taken_from_the_text(self):
        article = make_article(excerpt="", body="<h2>Title</h2><p>" + "alpha beta gamma " * 30 + "</p>")
        assert article.excerpt.startswith("Title alpha beta") and len(article.excerpt) <= 160
        assert "<" not in article.excerpt and not article.excerpt.endswith(" ")
        assert make_article("Own", excerpt="Mine").excerpt == "Mine"

    def test_publishing_without_a_date_sets_it_to_now(self):
        article = make_article("Fresh", published_at=None)
        assert article.published_at is not None and timezone.now() - article.published_at < timedelta(
            minutes=1
        )
        draft = make_article("Draft", is_published=False, published_at=None)
        assert draft.published_at is None

    def test_the_slug_is_unique(self):
        make_article("Same", slug="same")
        with pytest.raises(IntegrityError), transaction.atomic():
            make_article("Other", slug="same")

    def test_a_published_row_must_have_a_date_in_the_database(self):
        article = make_article("Row", is_published=False, published_at=None)
        with pytest.raises(IntegrityError), transaction.atomic():
            Article.objects.filter(pk=article.pk).update(is_published=True)  # bypasses save()

    def test_the_media_must_be_an_image_or_video_of_a_safe_type(self):
        article = Article(
            title="T",
            slug="t",
            body="x",
            media=SimpleUploadedFile("a.svg", b"<svg/>", content_type="image/svg+xml"),
        )
        with pytest.raises(ValidationError) as error:
            article.full_clean()
        assert "media" in error.value.message_dict

    def test_plain_text_helper(self):
        assert (
            plain_text("<p>One</p><p>Two &amp; three</p><ul><li>a</li><li>b</li></ul>")
            == "One Two & three a b"
        )
        assert plain_text("<script>alert(1)</script>ok") == "ok" and plain_text("") == ""


@pytest.mark.django_db
class TestArticleAdmin:
    @pytest.fixture
    def staff_client(self, client):
        admin = User.objects.create_superuser(
            email="root@example.com", name="Root Admin", mobile="9000000001", password="s3cret-pass-1"
        )
        client.force_login(admin)
        return client

    def test_the_form_uses_the_rich_text_editor_for_the_content(self, staff_client):
        html = staff_client.get(reverse("admin:content_article_add")).content.decode()
        assert html.count('data-richtext="1"') == 1 and 'name="body"' in html

    def test_creating_an_article_cleans_html_sets_the_author_and_publishes(self, staff_client):
        response = staff_client.post(
            reverse("admin:content_article_add"),
            {
                "title": "Line follower",
                "slug": "line-follower",
                "excerpt": "",
                "body": "<p>Build it <strong>fast</strong></p><script>alert(1)</script>",
                "media": SimpleUploadedFile("a.png", GIF, content_type="image/png"),
                "category": make_category("Tutorials").pk,
                "author": "",
                "is_published": "on",
                "published_at_0": "",
                "published_at_1": "",
            },
        )
        assert response.status_code == 302, (
            response.context["adminform"].form.errors if response.context else ""
        )
        article = Article.objects.get(slug="line-follower")
        assert "<script" not in article.body and "<strong>fast</strong>" in article.body
        assert article.author == "Root Admin" and article.is_published and article.published_at is not None
        assert article.created_by is not None
        assert APIClient().get(f"{LIST}/line-follower").status_code == 200

    def test_bulk_publish_and_unpublish(self, staff_client):
        draft = make_article("Draft", is_published=False, published_at=None)
        url = reverse("admin:content_article_changelist")
        staff_client.post(url, {"action": "publish_articles", "_selected_action": [str(draft.pk)]})
        draft.refresh_from_db()
        assert draft.is_published and draft.published_at is not None
        assert APIClient().get(f"{LIST}/{draft.slug}").status_code == 200

        staff_client.post(url, {"action": "unpublish_articles", "_selected_action": [str(draft.pk)]})
        draft.refresh_from_db()
        assert not draft.is_published
        assert APIClient().get(f"{LIST}/{draft.slug}").status_code == 404

    def test_publishing_keeps_a_date_staff_already_chose(self, staff_client):
        later = timezone.now() + timedelta(days=3)
        scheduled = make_article("Scheduled", is_published=False, published_at=later)
        staff_client.post(
            reverse("admin:content_article_changelist"),
            {"action": "publish_articles", "_selected_action": [str(scheduled.pk)]},
        )
        scheduled.refresh_from_db()
        assert scheduled.is_published and abs((scheduled.published_at - later).total_seconds()) < 1
        assert (
            APIClient().get(f"{LIST}/{scheduled.slug}").status_code == 404
        )  # live only when the date arrives

    def test_the_list_shows_thumbnails_and_sits_in_the_content_section(self, staff_client):
        make_article()
        page = staff_client.get(reverse("admin:content_article_changelist"))
        assert page.status_code == 200 and "object-fit:cover" in page.content.decode()
        sections = {
            s["name"]: [m["object_name"] for m in s["models"]] for s in page.context["available_apps"]
        }
        assert "Article" in sections["Content"]


@pytest.mark.django_db
def test_seed_articles_creates_the_samples_once():
    from django.core.management import call_command

    call_command("seed_articles")
    call_command("seed_articles")  # idempotent
    assert Article.objects.count() == 3
    res = APIClient().get(LIST)
    assert [a["slug"] for a in res.data][0] == "choosing-your-first-robot-kit"  # newest first
    assert all(a["readMinutes"] >= 1 and a["media"].endswith(".png") for a in res.data)
    assert {a["category"] for a in res.data} == {"Guides", "Comparison", "Tutorials"}
    assert ArticleCategory.objects.count() == 3


@pytest.mark.django_db
class TestMedia:
    def test_a_video_is_accepted_and_reported_as_a_video(self):
        article = make_article("Video", media=SimpleUploadedFile("demo.mp4", MP4, content_type="video/mp4"))
        assert article.media_type == "video"
        data = APIClient().get(f"{LIST}/{article.slug}").data
        assert data["mediaType"] == "video" and data["media"].endswith(".mp4")
        assert data["image"] == ""  # the old field only ever carries an image

    def test_the_type_follows_the_file_when_it_is_replaced(self):
        article = make_article("Swap")
        assert article.media_type == "image"
        article.media = SimpleUploadedFile("clip.mp4", MP4, content_type="video/mp4")
        article.save()
        assert article.media_type == "video"

    def test_a_fake_video_is_rejected(self):
        article = Article(
            title="T",
            slug="t",
            body="x",
            media=SimpleUploadedFile("a.mp4", b"not a video", content_type="video/mp4"),
        )
        with pytest.raises(ValidationError) as error:
            article.full_clean()
        assert "media" in error.value.message_dict

    def test_the_list_marks_videos_in_the_admin(self, client):
        make_article("Clip", media=SimpleUploadedFile("c.mp4", MP4, content_type="video/mp4"))
        client.force_login(
            User.objects.create_superuser(
                email="r@example.com", name="R", mobile="9000000001", password="s3cret-pass-1"
            )
        )
        html = client.get(reverse("admin:content_article_changelist")).content.decode()
        assert ">Video<" in html


@pytest.mark.django_db
class TestCategoryMaster:
    def test_names_are_unique_ignoring_case(self):
        make_category("Guides")
        with pytest.raises(IntegrityError), transaction.atomic():
            make_category("guides", slug="guides-2")

    def test_the_slug_is_unique(self):
        make_category("Guides")
        with pytest.raises(IntegrityError), transaction.atomic():
            make_category("Other", slug="guides")

    def test_a_category_in_use_cannot_be_deleted(self):
        category = make_category("Guides")
        make_article(category=category)
        from django.db.models import ProtectedError

        with pytest.raises(ProtectedError):
            category.delete()

    def test_an_article_may_have_no_category(self):
        article = make_article(category=None)
        data = APIClient().get(f"{LIST}/{article.slug}").data
        assert (data["category"], data["categorySlug"]) == ("", "")

    def test_the_category_list_has_active_categories_with_public_articles_only(self):
        guides, tutorials = make_category("Guides", sort_order=2), make_category("Tutorials", sort_order=1)
        unused, hidden = make_category("Unused"), make_category("Hidden", is_active=False)
        make_article("A", category=guides)
        make_article("B", category=guides)
        make_article("C", category=tutorials)
        make_article("Draft", category=unused, is_published=False)
        make_article("In hidden", category=hidden)
        make_article("Later", category=unused, published_at=timezone.now() + timedelta(days=1))
        res = APIClient().get("/api/v1/article-categories")
        assert res.status_code == 200
        assert res.data == [
            {"slug": "tutorials", "name": "Tutorials", "articleCount": 1},  # sort_order 1 comes first
            {"slug": "guides", "name": "Guides", "articleCount": 2},
        ]

    def test_the_list_can_be_filtered_by_category(self):
        guides, tutorials = make_category("Guides"), make_category("Tutorials")
        make_article("Guide one", category=guides)
        make_article("Tutorial one", category=tutorials)
        make_article("Uncategorised", category=None)
        client = APIClient()
        assert [a["title"] for a in client.get(f"{LIST}?category=guides").data] == ["Guide one"]
        assert client.get(f"{LIST}?category=nothing").data == []  # unknown slug: simply no articles
        assert len(client.get(LIST).data) == 3
        assert client.get(f"{LIST}?category=bad slug!").status_code == 400

    def test_the_list_still_uses_a_constant_number_of_queries(self, django_assert_max_num_queries):
        guides = make_category("Guides")
        for n in range(8):
            make_article(f"Post {n}", category=guides, published_at=timezone.now() - timedelta(hours=n + 1))
        with django_assert_max_num_queries(2):
            assert len(APIClient().get(LIST).data) == 8

    def test_categories_are_managed_in_the_admin(self, client):
        client.force_login(
            User.objects.create_superuser(
                email="r@example.com", name="R", mobile="9000000001", password="s3cret-pass-1"
            )
        )
        guides = make_category("Guides")
        make_article(category=guides)
        page = client.get(reverse("admin:content_articlecategory_changelist"))
        assert page.status_code == 200 and "Guides" in page.content.decode()
        form = client.get(reverse("admin:content_article_add")).content.decode()
        assert "related-widget-wrapper" in form or "select2" in form  # the category picker (autocomplete)
        sections = {
            s["name"]: [m["object_name"] for m in s["models"]] for s in page.context["available_apps"]
        }
        assert "ArticleCategory" in sections["Content"]
