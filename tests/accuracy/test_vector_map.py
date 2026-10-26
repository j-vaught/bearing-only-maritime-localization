"""Accuracy tests for indexed vector water and visibility adapters."""

from __future__ import annotations

import pytest
from pyproj import CRS, Transformer
from shapely import to_geojson
from shapely.geometry import Polygon, box

from maritime.geometry.vector_los import line_of_sight_clear
from maritime.geometry.vector_map import VectorMap
from maritime.schemas.inputs_v2 import MapV2

CRS_METRES = "EPSG:32618"
COVERAGE = box(-20.0, -20.0, 20.0, 20.0)


def make_map(
    land: Polygon | None,
    *,
    coverage: Polygon = COVERAGE,
    unknown: Polygon | None = None,
) -> VectorMap:
    return VectorMap(
        land,
        crs=CRS_METRES,
        coverage_geometry=coverage,
        unknown_geometry=unknown,
    )


def test_point_water_status_distinguishes_water_land_unknown_and_shore() -> None:
    land = box(4.0, -2.0, 6.0, 2.0)
    vector_map = make_map(land)

    assert vector_map.point_water_status((0.0, 0.0)) == "water"
    assert vector_map.point_water_status((5.0, 0.0)) == "land"
    assert vector_map.point_water_status((4.0, 0.0)) == "water"
    assert vector_map.point_water_status((21.0, 0.0)) == "unknown"
    assert vector_map.observer_valid((0.0, 0.0))
    assert not vector_map.observer_valid((5.0, 0.0))
    assert not vector_map.observer_valid((21.0, 0.0))


def test_known_polygon_hole_is_water_and_preserves_clear_channel() -> None:
    island_with_lagoon = Polygon(
        [(3.0, -3.0), (7.0, -3.0), (7.0, 3.0), (3.0, 3.0)],
        holes=[[(4.0, -1.0), (6.0, -1.0), (6.0, 1.0), (4.0, 1.0)]],
    )
    vector_map = make_map(island_with_lagoon)

    assert vector_map.point_water_status((3.5, 0.0)) == "land"
    assert vector_map.point_water_status((5.0, 0.0)) == "water"
    assert vector_map.line_visibility((4.2, 0.0), (5.8, 0.0)) == "clear"
    assert vector_map.line_visibility((0.0, 2.0), (10.0, 2.0)) == "blocked"


def test_island_crossing_blocks_but_narrow_channel_stays_clear() -> None:
    island = box(3.0, -2.0, 7.0, 2.0)
    vector_map = make_map(island)

    assert vector_map.line_visibility((0.0, 0.0), (10.0, 0.0)) == "blocked"
    assert vector_map.line_visibility((0.0, 2.0), (10.0, 2.0)) == "clear"
    assert vector_map.line_visibility((0.0, 0.0), (10.0, 0.0), indexed=False) == "blocked"


def test_tangency_shoreline_following_and_shoreline_endpoint_are_allowed() -> None:
    land = box(4.0, -1.0, 6.0, 1.0)
    vector_map = make_map(land)

    assert vector_map.line_visibility((0.0, 1.0), (10.0, 1.0)) == "clear"
    assert vector_map.line_visibility((0.0, 0.0), (4.0, 0.0)) == "clear"
    assert vector_map.line_visibility((4.0, 0.0), (0.0, 3.0)) == "clear"
    assert vector_map.line_visibility((5.0, 0.0), (10.0, 0.0)) == "blocked"


def test_unknown_region_overrides_water_and_unknown_ray_is_not_land() -> None:
    unknown = box(-1.0, -2.0, 1.0, 2.0)
    vector_map = make_map(box(5.0, -1.0, 6.0, 1.0), unknown=unknown)

    assert vector_map.point_water_status((0.0, 0.0)) == "unknown"
    assert vector_map.point_water_status((2.0, 0.0)) == "water"
    assert vector_map.line_visibility((0.0, 0.0), (2.0, 0.0)) == "unknown"
    assert vector_map.line_visibility((2.0, 0.0), (8.0, 0.0)) == "blocked"


def test_unknown_overlap_removes_land_from_known_visibility_index() -> None:
    land = box(4.0, -1.0, 6.0, 1.0)
    unknown = box(3.0, -2.0, 7.0, 2.0)
    vector_map = make_map(land, unknown=unknown)

    assert vector_map.point_water_status((5.0, 0.0)) == "unknown"
    assert vector_map.line_visibility((0.0, 0.0), (10.0, 0.0)) == "unknown"


def test_uncovered_area_and_unknown_map_are_tristate() -> None:
    partial_coverage = box(-5.0, -5.0, 5.0, 5.0)
    vector_map = make_map(None, coverage=partial_coverage)
    no_map = VectorMap(None, crs=CRS_METRES)

    assert vector_map.point_water_status((0.0, 0.0)) == "water"
    assert vector_map.point_water_status((6.0, 0.0)) == "unknown"
    assert vector_map.line_visibility((0.0, 0.0), (6.0, 0.0)) == "unknown"
    assert no_map.point_water_status((0.0, 0.0)) == "unknown"
    assert no_map.line_visibility((0.0, 0.0), (1.0, 0.0)) == "unknown"


def test_zero_length_ray_and_tolerance_contract() -> None:
    vector_map = make_map(box(4.0, -0.1, 4.001, 0.1))

    assert vector_map.line_visibility((0.0, 0.0), (0.0, 0.0)) == "clear"
    assert vector_map.line_visibility((4.0005, 0.0), (4.0005, 0.0)) == "blocked"
    assert vector_map.line_visibility((0.0, 0.0), (10.0, 0.0), tolerance_m=0.01) == "clear"
    assert vector_map.line_visibility((0.0, 0.0), (10.0, 0.0), tolerance_m=0.0001) == "blocked"


def test_strtree_index_matches_unindexed_geometry_reference() -> None:
    land = box(3.0, -1.0, 4.0, 1.0).union(box(6.0, 2.0, 7.0, 4.0))
    vector_map = VectorMap(land, crs=CRS_METRES, coverage_geometry=COVERAGE)
    rays = [
        ((0.0, 0.0), (10.0, 0.0)),
        ((0.0, 2.5), (10.0, 2.5)),
        ((0.0, 1.0), (10.0, 1.0)),
        ((0.0, -5.0), (10.0, -5.0)),
    ]

    for observer, target in rays:
        assert vector_map.line_visibility(observer, target) == vector_map.line_visibility(
            observer, target, indexed=False
        )


def test_overlapping_polygon_features_are_merged_before_indexing() -> None:
    overlapping_features = [box(4.0, -1.0, 5.0, 1.0), box(4.5, -1.0, 6.0, 1.0)]
    vector_map = VectorMap(
        overlapping_features,
        crs=CRS_METRES,
        coverage_geometry=COVERAGE,
    )

    assert vector_map.point_water_status((4.75, 0.0)) == "land"
    assert vector_map.line_visibility((0.0, 0.0), (10.0, 0.0)) == "blocked"
    assert vector_map.line_visibility((0.0, 0.0), (10.0, 0.0), indexed=False) == "blocked"


def test_legacy_ring_api_matches_indexed_adapter_for_known_water_cases() -> None:
    ring = ((4.0, -1.0), (6.0, -1.0), (6.0, 1.0), (4.0, 1.0))
    vector_map = make_map(box(4.0, -1.0, 6.0, 1.0))
    rays = [
        ((0.0, 0.0), (10.0, 0.0)),
        ((0.0, 1.0), (10.0, 1.0)),
        ((0.0, 3.0), (10.0, 3.0)),
        ((4.0, 0.0), (10.0, 0.0)),
    ]

    for observer, target in rays:
        legacy = line_of_sight_clear(observer, target, [ring])
        indexed = vector_map.line_visibility(observer, target) == "clear"
        assert indexed is legacy


def test_map_v2_rejects_geographic_degrees_without_explicit_target_crs() -> None:
    coverage = Polygon([(-76.0, 36.9), (-75.9, 36.9), (-75.9, 37.0), (-76.0, 37.0)])
    map_v2 = MapV2(
        crs="EPSG:4326",
        source="unit-test",
        map_version="1",
        coverage_status="known",
        coverage_geometry_geojson=to_geojson(coverage),
    )

    with pytest.raises(ValueError, match="projected.*metres"):
        VectorMap.from_map_v2(map_v2)
    vector_map = VectorMap.from_map_v2(map_v2, target_crs="EPSG:32618")
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True)
    x, y = transformer.transform(-75.95, 36.95)
    assert vector_map.crs == CRS.from_epsg(32618)
    assert vector_map.point_water_status((x, y)) == "water"


def test_invalid_polygon_and_geographic_direct_crs_are_rejected() -> None:
    invalid = Polygon([(0.0, 0.0), (2.0, 2.0), (0.0, 2.0), (2.0, 0.0)])
    with pytest.raises(ValueError, match="valid polygonal"):
        VectorMap(invalid, crs=CRS_METRES, coverage_geometry=COVERAGE)
    with pytest.raises(ValueError, match="projected.*metres"):
        VectorMap(None, crs="EPSG:4326", coverage_geometry=box(-1, -1, 1, 1))
