from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from .conftest import add_image, add_video, make_category, make_product, make_variant

pytestmark = pytest.mark.django_db
LIST = "/api/v1/products"


@pytest.fixture
def tree():
    robots = make_category("Robot Kits")
    rover = make_category("Rover Kits", parent=robots)
    drones = make_category("Drones")
    return robots, rover, drones


def slugs(response):
    return [p["slug"] for p in response.data["items"]]


class TestList:
    def test_envelope_and_item_shape(self, api, tree):
        robots, rover, _ = tree
        make_product(
            "Atlas Rover", rover, price=18999, compare_at_price=22999, rating=Decimal("4.8"), is_new=True
        )
        response = api.get(LIST)
        assert response.status_code == 200
        assert {"items", "page", "pageSize", "total", "totalPages"} == set(response.data)
        item = response.data["items"][0]
        assert item["categorySlug"] == "robot-kits" and item["categoryName"] == "Robot Kits"
        assert item["price"] == 18999 and item["compareAtPrice"] == 22999
        assert item["rating"] == 4.8 and item["inStock"] is True and item["isNew"] is True
        assert item["image"].startswith("http://testserver/")
        assert isinstance(item["id"], str)

    def test_compare_at_price_omitted_when_not_discounted(self, api, tree):
        make_product("Plain", tree[2])
        assert "compareAtPrice" not in api.get(LIST).data["items"][0]

    def test_out_of_stock_flag(self, api, tree):
        make_product("Gone", tree[2], stock_quantity=0)
        assert api.get(LIST).data["items"][0]["inStock"] is False

    def test_hidden_products_and_categories(self, api, tree):
        robots, rover, drones = tree
        make_product("Visible", drones)
        make_product("Inactive", drones, is_active=False)
        hidden_cat = make_category("Hidden", is_active=False)
        make_product("InHiddenCat", hidden_cat)
        hidden_parent = make_category("Retired", is_active=False)
        make_product("InChildOfRetired", make_category("Sub", parent=hidden_parent))
        assert slugs(api.get(LIST)) == ["visible"]

    def test_pagination(self, api, tree):
        for i in range(5):
            make_product(f"P{i}", tree[2])
        response = api.get(LIST, {"page": 2, "pageSize": 2})
        assert (
            response.data["page"],
            response.data["pageSize"],
            response.data["total"],
            response.data["totalPages"],
        ) == (2, 2, 5, 3)
        assert len(response.data["items"]) == 2
        assert api.get(LIST, {"page": 99}).status_code == 404

    def test_category_filter_includes_children_but_child_filter_is_exact(self, api, tree):
        robots, rover, drones = tree
        make_product("Top Level", robots)
        make_product("Child One", rover)
        make_product("Drone", drones)
        assert set(slugs(api.get(LIST, {"category": "robot-kits"}))) == {"top-level", "child-one"}
        assert slugs(api.get(LIST, {"category": "rover-kits"})) == ["child-one"]
        assert slugs(api.get(LIST, {"category": "nonexistent"})) == []

    def test_text_search(self, api, tree):
        make_product("ESP32 Board", tree[2], summary="Wi-Fi")
        make_product("Motor", tree[2], summary="has esp32 inside")
        make_product("Other", tree[2])
        assert set(slugs(api.get(LIST, {"q": "esp32"}))) == {"esp32-board", "motor"}
        assert len(slugs(api.get(LIST, {"q": "  "}))) == 3

    def test_price_filter_and_validation(self, api, tree):
        for name, price in (("A", 100), ("B", 500), ("C", 900)):
            make_product(name, tree[2], price=price)
        assert slugs(api.get(LIST, {"minPrice": 200, "maxPrice": 600})) == ["b"]
        assert api.get(LIST, {"minPrice": 600, "maxPrice": 200}).status_code == 400
        assert api.get(LIST, {"minPrice": "abc"}).status_code == 400

    def test_sorting(self, api, tree):
        make_product("A", tree[2], price=300, sold_count=1)
        make_product("B", tree[2], price=100, sold_count=50)
        make_product("C", tree[2], price=200, sold_count=10)
        assert slugs(api.get(LIST, {"sort": "price-asc"})) == ["b", "c", "a"]
        assert slugs(api.get(LIST, {"sort": "price-desc"})) == ["a", "c", "b"]
        assert slugs(api.get(LIST, {"sort": "bestselling"})) == ["b", "c", "a"]
        assert slugs(api.get(LIST, {"sort": "newest"})) == ["c", "b", "a"]
        response = api.get(LIST, {"sort": "bogus"})
        assert response.status_code == 400 and "sort" in response.data["errors"]

    def test_constant_query_count(self, api, tree):
        make_product("One", tree[1])
        with CaptureQueriesContext(connection) as small:
            api.get(LIST)
        for i in range(11):
            make_product(f"More {i}", tree[1])
        with CaptureQueriesContext(connection) as large:
            api.get(LIST)
        assert len(large) == len(small)

    def test_public_no_auth_needed_and_invalid_token_not_required(self, api, tree):
        assert api.get(LIST).status_code == 200


class TestFilters:
    def test_price_range_for_category_and_search(self, api, tree):
        robots, rover, drones = tree
        make_product("Cheap Rover", rover, price=100)
        make_product("Dear Rover", rover, price=900)
        make_product("Drone", drones, price=5000)
        data = api.get("/api/v1/products/filters", {"category": "robot-kits"}).data
        assert data == {"priceRange": {"min": 100, "max": 900}}
        assert api.get("/api/v1/products/filters", {"q": "drone"}).data["priceRange"] == {
            "min": 5000,
            "max": 5000,
        }

    def test_empty_result_is_zero_range(self, api, tree):
        assert api.get("/api/v1/products/filters", {"category": "x"}).data == {
            "priceRange": {"min": 0, "max": 0}
        }


class TestDetail:
    def test_detail_with_images_and_variants(self, api, tree):
        product = make_product(
            "Atlas Rover", tree[1], price=1000, description="Para one.\n\nPara two.", variant_label="Edition"
        )
        add_image(product, position=1, alt="side view")
        make_variant(product, "Large", 1300, type="size", position=1, image_index=1, in_stock=False)
        make_variant(product, "Red", 1000, type="color", position=0, compare_at_price=1200)
        data = api.get("/api/v1/products/atlas-rover").data
        assert data["description"].startswith("Para one.")
        assert len(data["images"]) == 2 and data["images"][1]["alt"] == "side view"
        assert data["variantLabel"] == "Edition"
        assert [v["label"] for v in data["variants"]] == ["Red", "Large"]
        assert [(v["type"], v["slug"]) for v in data["variants"]] == [("color", "red"), ("size", "large")]
        assert data["variants"][0]["compareAtPrice"] == 1200 and "compareAtPrice" not in data["variants"][1]
        assert data["variants"][1]["inStock"] is False and data["variants"][1]["imageIndex"] == 1

    def test_videos_listed_separately_and_never_used_as_thumbnail(self, api, tree):
        product = make_product("Demo", tree[2], image=False)
        add_video(product, position=0, alt="Unboxing")  # earliest position, but must not become the thumbnail
        add_image(product, position=1, alt="front")
        detail = api.get("/api/v1/products/demo").data
        assert [i["alt"] for i in detail["images"]] == ["front"]
        assert [v["alt"] for v in detail["videos"]] == ["Unboxing"]
        assert detail["videos"][0]["url"].endswith(".mp4")
        listed = api.get(LIST).data["items"][0]
        assert listed["image"].endswith(".gif") and "videos" not in listed

    def test_product_with_only_video_has_empty_thumbnail(self, api, tree):
        add_video(make_product("Clip", tree[2], image=False))
        assert api.get(LIST).data["items"][0]["image"] == ""

    def test_default_variant_label_and_no_variants(self, api, tree):
        make_product("Plain", tree[2])
        data = api.get("/api/v1/products/plain").data
        assert data["variantLabel"] == "Option" and data["variants"] == []

    def test_unknown_and_inactive_return_404_with_safe_message(self, api, tree):
        make_product("Hidden", tree[2], is_active=False)
        for slug in ("nope", "hidden"):
            response = api.get(f"/api/v1/products/{slug}")
            assert response.status_code == 404 and response.data == {"message": "Not found."}

    def test_filters_route_not_treated_as_slug(self, api, tree):
        assert api.get("/api/v1/products/filters").status_code == 200

    def test_detail_query_count_constant(self, api, tree):
        product = make_product("Q", tree[1])
        for i in range(3):
            add_image(product, position=i + 1)
            make_variant(product, f"V{i}", 100 + i, type="color")
        with CaptureQueriesContext(connection) as ctx:
            api.get("/api/v1/products/q")
        assert len(ctx) <= 4  # product(+category), media, variants


class TestRelated:
    def test_same_family_first_max_four_excludes_self(self, api, tree):
        robots, rover, drones = tree
        target = make_product("Target", rover, sold_count=1)
        sibling = make_product("Sibling", robots, sold_count=5)
        for i in range(5):
            make_product(f"Drone {i}", drones, sold_count=1000 + i)
        data = api.get(f"/api/v1/products/{target.slug}/related").data
        assert len(data) == 4
        assert data[0]["slug"] == sibling.slug
        assert target.slug not in {p["slug"] for p in data}

    def test_404_for_unknown(self, api):
        assert api.get("/api/v1/products/nope/related").status_code == 404


class TestSuggestions:
    def test_limit_and_shape(self, api, tree):
        for i in range(8):
            make_product(f"ESP Board {i}", tree[2], sold_count=i)
        data = api.get("/api/v1/search/suggestions", {"q": "esp"}).data
        assert len(data) == 5
        assert set(data[0]) == {"slug", "name", "categoryName", "price", "image"}
        assert data[0]["name"] == "ESP Board 7"  # best sellers first

    @pytest.mark.parametrize("params", [{}, {"q": ""}, {"q": "   "}])
    def test_blank_query_returns_empty_list(self, api, params):
        assert api.get("/api/v1/search/suggestions", params).data == []
