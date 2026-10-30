"""Polygon clipping can produce mixed collections without changing land area."""

import pytest
from shapely.geometry import MultiPolygon, Polygon, box

from maritime.geometry.vector_map import VectorMap


def mixed_map(*, unknown=None):
    island = Polygon(
        [(3, 2), (7, 2), (7, 8), (3, 8)],
        [[(4, 3), (6, 3), (6, 7), (4, 7)]],
    )
    land = MultiPolygon([island, box(10, 2, 12, 4), box(10, 10, 12, 12)])
    coverage = box(0, 0, 10, 10)
    assert land.is_valid and coverage.is_valid
    expected = land.intersection(coverage)
    assert expected.geom_type == "GeometryCollection"
    assert {part.geom_type for part in expected.geoms} == {"Polygon", "LineString", "Point"}
    if unknown is not None:
        expected = expected.difference(unknown)
    result = VectorMap(land, crs="EPSG:32618", coverage_geometry=coverage, unknown_geometry=unknown)
    # Index/point-predicate repair must not rewrite geometry used by caches.
    assert result.land_geometry.wkb == expected.wkb
    return result


@pytest.mark.parametrize("indexed", [True, False])
def test_clipped_polygon_in_collection_blocks_land_interior(indexed):
    vm = mixed_map()
    assert vm.line_visibility((1, 5), (9, 5), indexed=indexed) == "blocked"
    assert vm.line_visibility((4.5, 5), (9, 5), indexed=indexed) == "blocked"
    assert vm.line_visibility((3.5, 5), (1, 5), indexed=indexed) == "blocked"


def test_mixed_collection_points_preserve_holes_and_boundary_semantics():
    vm = mixed_map()
    expected = {
        (3.5, 5): "land",
        (5, 5): "water",
        (3, 5): "water",
        (10, 3): "water",  # Line-only clipping remnant.
        (10, 10): "water",  # Point-only clipping remnant.
        (1, 5): "water",
        (11, 3): "unknown",
    }
    for point, status in expected.items():
        assert vm.point_water_status(point) == status
        assert vm.observer_valid(point) is (status == "water")


@pytest.mark.parametrize("indexed", [True, False])
def test_shoreline_only_contact_and_hole_rays_remain_clear(indexed):
    vm = mixed_map()
    for observer, target in (
        ((1, 2), (9, 2)),
        ((1, 5), (3, 5)),
        ((4.5, 5), (5.5, 5)),
        ((9, 3), (10, 3)),
        ((9, 9), (10, 10)),
    ):
        assert vm.line_visibility(observer, target, indexed=indexed) == "clear"
    assert vm.line_visibility((10, 3), (9, 3), indexed=indexed) == "clear"
    assert vm.line_visibility_many(
        (10, 3),
        ((9, 3), (10, 2), (10, 4)),
        indexed=indexed,
    ) == ("clear", "clear", "clear")
    assert vm.line_visibility_many(
        (10, 10),
        ((9, 9), (9, 10), (10, 3)),
        indexed=indexed,
    ) == ("clear", "clear", "clear")


@pytest.mark.parametrize("indexed", [True, False])
def test_batch_clipping_agrees_with_individual_collection_queries(indexed):
    vm = mixed_map()
    targets = ((9, 5), (3, 5), (2, 5), (5, 5))
    expected = ("blocked", "clear", "clear", "blocked")
    assert vm.line_visibility_many((1, 5), targets, indexed=indexed) == expected
    assert tuple(vm.line_visibility((1, 5), p, indexed=indexed) for p in targets) == expected


@pytest.mark.parametrize("indexed", [True, False])
def test_unknown_regions_stay_unknown_and_known_land_still_blocks(indexed):
    vm = mixed_map(unknown=box(8, 4, 9, 6))
    assert vm.point_water_status((8.5, 5)) == "unknown"
    assert vm.point_water_status((3.5, 5)) == "land"
    assert vm.line_visibility((1, 5), (9.5, 5), indexed=indexed) == "blocked"
    assert vm.line_visibility((8.5, 5), (9.5, 5), indexed=indexed) == "unknown"
    targets = ((8.5, 9), (8.5, 3), (8.5, 5))
    expected = ("unknown", "clear", "unknown")
    assert vm.line_visibility_many((8.5, 1), targets, indexed=indexed) == expected
    assert tuple(vm.line_visibility((8.5, 1), p, indexed=indexed) for p in targets) == expected
