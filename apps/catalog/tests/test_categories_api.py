import pytest

from .conftest import make_category, make_product

pytestmark = pytest.mark.django_db


@pytest.fixture
def tree():
    robots = make_category("Robot Kits", description="Build robots")
    rover = make_category("Rover Kits", parent=robots)
    walkers = make_category("Walking Robots", parent=robots)
    drones = make_category("Drones")
    return robots, rover, walkers, drones


def test_tree_with_counts_including_children(api, tree):
    robots, rover, walkers, drones = tree
    make_product("Direct", robots)
    make_product("R1", rover)
    make_product("R2", rover)
    make_product("Off", rover, is_active=False)
    data = api.get("/api/v1/categories").data
    by_slug = {c["slug"]: c for c in data}
    assert set(by_slug) == {"robot-kits", "drones"}
    assert by_slug["robot-kits"]["productCount"] == 3
    assert by_slug["drones"]["productCount"] == 0
    assert {c["slug"] for c in by_slug["robot-kits"]["children"]} == {"rover-kits", "walking-robots"}
    assert set(by_slug["robot-kits"]["children"][0]) == {"slug", "name"}
    assert "parent" not in by_slug["robot-kits"]


def test_inactive_categories_hidden(api, tree):
    robots, rover, walkers, drones = tree
    walkers.is_active = False
    walkers.save()
    drones.is_active = False
    drones.save()
    data = api.get("/api/v1/categories").data
    assert [c["slug"] for c in data] == ["robot-kits"]
    assert [c["slug"] for c in data[0]["children"]] == ["rover-kits"]


def test_top_level_detail(api, tree):
    make_product("X", tree[1])
    data = api.get("/api/v1/categories/robot-kits").data
    assert data["productCount"] == 1 and len(data["children"]) == 2 and "parent" not in data


def test_sub_category_detail_has_parent_and_no_children(api, tree):
    make_product("X", tree[1])
    data = api.get("/api/v1/categories/rover-kits").data
    assert data["parent"] == {"slug": "robot-kits", "name": "Robot Kits"}
    assert data["children"] == [] and data["productCount"] == 1


def test_unknown_or_inactive_detail_404(api, tree):
    tree[3].is_active = False
    tree[3].save()
    for slug in ("nope", "drones"):
        assert api.get(f"/api/v1/categories/{slug}").status_code == 404


def test_category_tree_query_count_constant(api, tree):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as small:
        api.get("/api/v1/categories")
    for i in range(5):
        parent = make_category(f"Extra {i}")
        make_category(f"Extra child {i}", parent=parent)
    with CaptureQueriesContext(connection) as large:
        api.get("/api/v1/categories")
    assert len(large) == len(small)
