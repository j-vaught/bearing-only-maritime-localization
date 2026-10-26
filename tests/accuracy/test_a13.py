"""Accuracy checks for exact planar and WGS84 geographic spatial queries."""

from __future__ import annotations

import random
from math import hypot

import pytest
from pyproj import Geod

from maritime_localization_shared.geospatial.coordinates import geodesic_bearing_distance
from maritime_localization_shared.geospatial.spatial_index import (
    GeographicSpatialIndex,
    SpatialIndex2D,
)

_GEOD = Geod(ellps="WGS84")


def test_planar_query_matches_brute_force_for_seeded_catalog_and_radii() -> None:
    generator = random.Random(20261025)
    points = [
        (generator.uniform(-20_000.0, 20_000.0), generator.uniform(-20_000.0, 20_000.0))
        for _ in range(800)
    ]
    index = SpatialIndex2D(points, cell_size_m=317.0)
    for _ in range(80):
        east, north = generator.uniform(-25_000.0, 25_000.0), generator.uniform(-25_000.0, 25_000.0)
        radius = generator.choice((0.0, 1.0, 317.0, 1_000.0, 8_000.0))
        expected = tuple(
            position
            for position, point in enumerate(points)
            if hypot(point[0] - east, point[1] - north) <= radius
        )
        assert index.query_radius(east, north, radius) == expected


def test_planar_index_dense_bucket_has_no_neighbor_cap_and_preserves_duplicate_keys() -> None:
    keys = tuple(f"record-{position:04}" for position in range(1_500))
    points = [(12.0, -5.0)] * len(keys)
    index = SpatialIndex2D(points, cell_size_m=500.0, keys=keys)

    assert index.query_radius(12.0, -5.0, 0.0) == tuple(range(1_500))
    assert index.query_radius_keys(12.0, -5.0, 0.0) == keys


def test_planar_keyed_rebuild_moves_records_without_changing_catalog_indices() -> None:
    index = SpatialIndex2D(
        [(0.0, 0.0), (10.0, 0.0), (-10.0, 0.0)],
        cell_size_m=2.0,
        keys=("a", "b", "c"),
    )
    moved = index.with_updated_points({"b": (1.0, 0.0), "c": (100.0, 0.0)})

    assert index.query_radius_keys(0.0, 0.0, 2.0) == ("a",)
    assert moved.query_radius(0.0, 0.0, 2.0) == (0, 1)
    assert moved.query_radius_keys(0.0, 0.0, 2.0) == ("a", "b")
    assert moved.index_for_key("c") == 2
    assert moved.keys == ("a", "b", "c")


def test_planar_keys_must_be_unique_and_match_point_count() -> None:
    with pytest.raises(ValueError, match="one key"):
        SpatialIndex2D([(0.0, 0.0)], keys=())
    with pytest.raises(ValueError, match="unique"):
        SpatialIndex2D([(0.0, 0.0), (1.0, 0.0)], keys=("same", "same"))


def test_geographic_radius_includes_exact_wgs84_boundary_and_duplicate_locations() -> None:
    origin = (-75.0, 30.0)
    boundary = _GEOD.fwd(*origin, 37.0, 10_000.0)[:2]
    points = [origin, boundary, origin]
    index = GeographicSpatialIndex(points, keys=("origin", "boundary", "duplicate"))

    assert index.query_radius(*origin, 0.0) == (0, 2)
    boundary_distance = geodesic_bearing_distance(origin, boundary).distance_m
    assert index.query_radius_keys(*origin, boundary_distance) == (
        "origin",
        "boundary",
        "duplicate",
    )
    assert index.query_radius(*origin, boundary_distance - 0.01) == (0, 2)


def test_geographic_radius_crosses_antimeridian_without_flat_projection_assumptions() -> None:
    points = [(179.9, 10.0), (-179.9, 10.0), (179.0, 10.0)]
    index = GeographicSpatialIndex(points, keys=("west", "east", "far"))
    assert index.query_radius_keys(179.9, 10.0, 22_000.0) == ("west", "east")


def test_geographic_index_handles_wide_latitude_coverage_and_empty_catalog() -> None:
    origin = (-69.45, 45.25)
    nearby = _GEOD.fwd(*origin, 45.0, 50_000.0)[:2]
    points = [(-80.19, 25.76), origin, nearby]
    index = GeographicSpatialIndex(points, keys=("miami", "maine", "maine-nearby"))
    assert index.query_radius_keys(-80.19, 25.76, 100_000.0) == ("miami",)
    assert index.query_radius_keys(*origin, 100_000.0) == ("maine", "maine-nearby")
    assert GeographicSpatialIndex([]).query_radius(-80.0, 25.0, 1_000.0) == ()


def test_geographic_keyed_rebuild_keeps_indices_and_exact_results() -> None:
    index = GeographicSpatialIndex([(0.0, 0.0), (10.0, 0.0)], keys=("a", "b"))
    moved = index.with_updated_points({"b": (0.005, 0.0)})

    assert index.query_radius_keys(0.0, 0.0, 1_000.0) == ("a",)
    assert moved.query_radius(0.0, 0.0, 1_000.0) == (0, 1)
    assert moved.index_for_key("b") == 1


def test_geographic_query_matches_wgs84_brute_force_with_seeded_catalog() -> None:
    generator = random.Random(20261025)
    points = [
        (generator.uniform(-179.9, 179.9), generator.uniform(-80.0, 80.0)) for _ in range(500)
    ]
    index = GeographicSpatialIndex(points)
    for _ in range(30):
        query = (generator.uniform(-180.0, 180.0), generator.uniform(-80.0, 80.0))
        radius = generator.choice((0.0, 50_000.0, 250_000.0, 1_000_000.0))
        expected = []
        for position, point in enumerate(points):
            distance = geodesic_bearing_distance(query, point).distance_m
            if distance <= radius:
                expected.append(position)
        assert index.query_radius(*query, radius) == tuple(expected)


@pytest.mark.parametrize("point", [(181.0, 0.0), (0.0, -91.0), (float("nan"), 0.0)])
def test_geographic_index_rejects_invalid_points(point: tuple[float, float]) -> None:
    with pytest.raises(ValueError, match="WGS84"):
        GeographicSpatialIndex([point])


@pytest.mark.parametrize("radius", [-1.0, float("inf"), float("nan")])
def test_geographic_index_rejects_invalid_radius(radius: float) -> None:
    index = GeographicSpatialIndex([(0.0, 0.0)])
    with pytest.raises(ValueError, match="radius_m"):
        index.query_radius(0.0, 0.0, radius)
