"""Exact CPU/GPU shoreline query parity on synthetic polygon edge cases."""

from __future__ import annotations

import pytest
from shapely.geometry import Polygon, box

from maritime.geometry.vector_map import VectorMap
from maritime.search.gpu.shoreline import GpuShorelineIndex


@pytest.fixture
def shoreline_pair() -> tuple[VectorMap, GpuShorelineIndex, int]:
    coverage = box(0.0, 0.0, 20.0, 20.0)
    land = Polygon(
        [(8.0, 0.0), (12.0, 0.0), (12.0, 20.0), (8.0, 20.0)],
        holes=[[(9.75, 1.0), (10.25, 1.0), (10.25, 19.0), (9.75, 19.0)]],
    )
    reference = VectorMap(land, crs="EPSG:32618", coverage_geometry=coverage)
    cp = pytest.importorskip("cupy")
    try:
        if cp.cuda.runtime.getDeviceCount() < 1:
            pytest.skip("No CUDA device is available.")
    except Exception as error:
        pytest.skip(f"CUDA runtime is unavailable: {error}")
    return reference, GpuShorelineIndex(reference), 0


def test_candidate_classification_matches_vector_map_edge_cases(shoreline_pair) -> None:
    reference, index, device_id = shoreline_pair
    points = (
        (2.0, 2.0),
        (10.0, 5.0),
        (9.75, 5.0),
        (8.0, 5.0),
        (8.1, 5.0),
        (3.0, 11.0),
        (-1.0, 5.0),
        (20.0, 5.0),
    )
    expected = tuple(reference.point_water_status(point) for point in points)
    assert expected == ("water", "water", "water", "water", "land", "water", "unknown", "water")
    assert index.classify_candidates(points, device_id=device_id) == expected


def test_batched_visibility_matches_scalar_vector_map(shoreline_pair) -> None:
    reference, index, device_id = shoreline_pair
    observer = (2.0, 2.0)
    targets = ((6.0, 2.0), (10.0, 2.0), (8.0, 2.0), (10.0, 18.0), (2.0, 21.0))
    expected = tuple(reference.line_visibility(observer, target) for target in targets)
    assert expected == ("clear", "blocked", "clear", "blocked", "unknown")
    assert index.visibility_many(observer, targets, device_id=device_id) == expected


def test_shoreline_grazing_and_channel_visibility_match_reference(shoreline_pair) -> None:
    reference, index, device_id = shoreline_pair
    cases = (
        ((2.0, 8.0), (8.0, 8.0)),
        ((8.0, 1.0), (8.0, 19.0)),
        ((6.0, -2.0), (10.0, 2.0)),
        ((10.0, 1.0), (10.0, 19.0)),
        ((10.0, 1.0), (11.0, 19.0)),
        ((8.0, 5.0), (10.0, 5.0)),
    )
    expected = tuple(reference.line_visibility(observer, target) for observer, target in cases)
    assert expected == ("clear", "clear", "unknown", "clear", "blocked", "blocked")
    for (observer, target), visibility in zip(cases, expected, strict=True):
        assert index.visibility_many(observer, (target,), device_id=device_id) == (visibility,)


def test_explicit_unknown_map_matches_vector_map(shoreline_pair) -> None:
    reference, _, _ = shoreline_pair
    partial = VectorMap(
        reference.land_geometry,
        crs=reference.crs,
        coverage_geometry=box(0.0, 0.0, 20.0, 20.0),
        unknown_geometry=box(2.0, 10.0, 4.0, 12.0),
    )
    index = GpuShorelineIndex(partial)
    points = ((3.0, 11.0), (5.0, 11.0), (9.0, 11.0))
    assert index.classify_candidates(points) == tuple(
        partial.point_water_status(point) for point in points
    )
    observer = (1.0, 11.0)
    targets = ((6.0, 11.0), (10.0, 11.0))
    assert index.visibility_many(observer, targets) == partial.line_visibility_many(
        observer, targets
    )


@pytest.mark.parametrize(
    ("width_m", "expected"),
    ((0.5e-6, "clear"), (1.0e-6, "clear"), (1.5e-6, "blocked")),
)
def test_land_interior_overlap_respects_tolerance(width_m, expected) -> None:
    coverage = box(0.0, 0.0, 20.0, 10.0)
    land = box(10.0, 2.0, 10.0 + width_m, 8.0)
    vector_map = VectorMap(land, crs="EPSG:32618", coverage_geometry=coverage)
    index = GpuShorelineIndex(vector_map)
    observer, target = (1.0, 5.0), (19.0, 5.0)
    assert vector_map.line_visibility(observer, target, tolerance_m=1e-6) == expected
    assert index.visibility_many(observer, (target,), tolerance_m=1e-6, device_id=0) == (expected,)


def test_explicit_disjoint_unknown_map_is_supported(shoreline_pair) -> None:
    reference, _, _ = shoreline_pair
    disjoint_unknown = VectorMap(
        reference.land_geometry,
        crs=reference.crs,
        coverage_geometry=box(0.0, 0.0, 20.0, 20.0),
        unknown_geometry=box(30.0, 30.0, 31.0, 31.0),
    )
    index = GpuShorelineIndex(disjoint_unknown)
    points = ((2.0, 2.0), (8.0, 5.0), (21.0, 5.0))
    assert index.classify_candidates(points) == tuple(
        disjoint_unknown.point_water_status(point) for point in points
    )


def test_cpu_fallback_counters_include_only_ambiguous_cases() -> None:
    coverage = box(0.0, 0.0, 20.0, 20.0)
    land = box(8.0, 0.0, 12.0, 20.0)
    vector_map = VectorMap(land, crs="EPSG:32618", coverage_geometry=coverage)
    index = GpuShorelineIndex(vector_map)
    points = ((2.0, 2.0), (9.0, 2.0), (8.0, 5.0), (21.0, 5.0))
    assert index.classify_candidates(points) == tuple(
        vector_map.point_water_status(point) for point in points
    )
    assert index.point_gpu_count == 4
    assert index.point_cpu_fallback_count == 1

    rays = ((6.0, 2.0), (14.0, 2.0), (8.0, 2.0), (2.0, 21.0))
    expected = tuple(vector_map.line_visibility((2.0, 2.0), target) for target in rays)
    assert index.visibility_many((2.0, 2.0), rays) == expected
    assert index.visibility_gpu_count == 4
    assert index.visibility_cpu_fallback_count == 1


def test_land_interior_targets_block_after_ray_edge_pruning() -> None:
    coverage = box(-10.0, -10.0, 20.0, 20.0)
    land = box(8.0, 0.0, 12.0, 10.0)
    vector_map = VectorMap(land, crs="EPSG:32618", coverage_geometry=coverage)
    index = GpuShorelineIndex(vector_map)
    observer = (0.0, 5.0)
    targets = ((9.0, 5.0), (10.0, 5.0), (11.0, 5.0))
    expected = tuple(vector_map.line_visibility(observer, target) for target in targets)
    assert expected == ("blocked", "blocked", "blocked")
    assert index.visibility_many(observer, targets) == expected
