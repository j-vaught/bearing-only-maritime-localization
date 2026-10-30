"""GPU shoreline parity for polygonal coverage and overlapping unknown regions."""

from __future__ import annotations

import pytest
from shapely.geometry import Polygon, box

from maritime.geometry.vector_map import VectorMap
from maritime.search.gpu.shoreline import GpuShorelineIndex


@pytest.fixture(scope="module")
def cupy_device() -> int:
    cp = pytest.importorskip("cupy")
    try:
        if cp.cuda.runtime.getDeviceCount() < 1:
            pytest.skip("No CUDA device is available.")
    except Exception as error:
        pytest.skip(f"CUDA runtime is unavailable: {error}")
    return 0


def test_concave_coverage_classification_and_exit_reentry(cupy_device: int) -> None:
    coverage = Polygon([(0, 0), (10, 0), (10, 10), (7, 10), (7, 3), (3, 3), (3, 10), (0, 10)])
    vector_map = VectorMap(None, crs="EPSG:32618", coverage_geometry=coverage)
    index = GpuShorelineIndex(vector_map)
    points = ((1, 8), (9, 8), (5, 8), (5, 1), (-1, 1))
    assert index.classify_candidates(points, device_id=cupy_device) == tuple(
        vector_map.point_water_status(point) for point in points
    )
    observer, targets = (1, 8), ((9, 8), (1, 1), (5, 8))
    assert index.visibility_many(observer, targets, device_id=cupy_device) == (
        vector_map.line_visibility_many(observer, targets)
    )


def test_coverage_hole_and_ray_boundary_grazing(cupy_device: int) -> None:
    coverage = Polygon(
        [(0, 0), (10, 0), (10, 10), (0, 10)], holes=[[(4, 4), (6, 4), (6, 6), (4, 6)]]
    )
    vector_map = VectorMap(None, crs="EPSG:32618", coverage_geometry=coverage)
    index = GpuShorelineIndex(vector_map)
    cases = (((1, 5), (9, 5)), ((1, 4), (9, 4)), ((1, 3), (9, 3)))
    for observer, target in cases:
        assert index.visibility_many(observer, (target,), device_id=cupy_device) == (
            vector_map.line_visibility(observer, target),
        )


def test_overlapping_unknown_precedence_and_land_erasure(cupy_device: int) -> None:
    coverage = box(0, 0, 10, 10)
    land = box(2, 2, 8, 8)
    unknown = box(4, 1, 6, 9)
    vector_map = VectorMap(
        land, crs="EPSG:32618", coverage_geometry=coverage, unknown_geometry=unknown
    )
    index = GpuShorelineIndex(vector_map)
    points = ((3, 3), (5, 3), (5, 1), (9, 9))
    assert index.classify_candidates(points, device_id=cupy_device) == tuple(
        vector_map.point_water_status(point) for point in points
    )
    cases = (
        ((1, 3), (9, 3)),  # known land is crossed before the unknown strip
        ((1, 1), (9, 1)),  # unknown strip only
        ((1, 5), (9, 5)),  # known land before and after unknown
        ((1, 1), (4, 1)),  # unknown boundary endpoint
    )
    for observer, target in cases:
        assert index.visibility_many(observer, (target,), device_id=cupy_device) == (
            vector_map.line_visibility(observer, target),
        )


def test_unknown_tangency_and_unknown_boundary_status(cupy_device: int) -> None:
    coverage = box(0, 0, 10, 10)
    unknown = box(4, 4, 6, 6)
    vector_map = VectorMap(
        None, crs="EPSG:32618", coverage_geometry=coverage, unknown_geometry=unknown
    )
    index = GpuShorelineIndex(vector_map)
    assert index.classify_candidates(((5, 5), (4, 5), (2, 5)), device_id=cupy_device) == (
        "unknown",
        "unknown",
        "water",
    )
    cases = (((1, 4), (9, 4)), ((1, 5), (4, 5)), ((1, 3), (9, 3)))
    for observer, target in cases:
        assert index.visibility_many(observer, (target,), device_id=cupy_device) == (
            vector_map.line_visibility(observer, target),
        )


@pytest.mark.parametrize(
    ("width_m", "expected"),
    ((0.5e-6, "clear"), (1.5e-6, "unknown")),
)
def test_unknown_overlap_respects_ray_tolerance(
    cupy_device: int, width_m: float, expected: str
) -> None:
    coverage = box(0, 0, 20, 10)
    unknown = box(10, 2, 10 + width_m, 8)
    vector_map = VectorMap(
        None,
        crs="EPSG:32618",
        coverage_geometry=coverage,
        unknown_geometry=unknown,
    )
    index = GpuShorelineIndex(vector_map)
    observer, target = (1, 5), (19, 5)
    assert vector_map.line_visibility(observer, target, tolerance_m=1e-6) == expected
    assert index.visibility_many(observer, (target,), tolerance_m=1e-6, device_id=cupy_device) == (
        expected,
    )


def test_unknown_erases_overlapping_land_for_unknown_only_los(cupy_device: int) -> None:
    coverage = box(0, 0, 10, 10)
    land = box(4, 4, 6, 6)
    unknown = box(3, 3, 7, 7)
    vector_map = VectorMap(
        land,
        crs="EPSG:32618",
        coverage_geometry=coverage,
        unknown_geometry=unknown,
    )
    index = GpuShorelineIndex(vector_map)
    observer, target = (1, 5), (9, 5)
    assert vector_map.point_water_status((5, 5)) == "unknown"
    assert vector_map.line_visibility(observer, target) == "unknown"
    assert index.classify_candidates(((5, 5),), device_id=cupy_device) == ("unknown",)
    assert index.visibility_many(observer, (target,), device_id=cupy_device) == ("unknown",)


def test_missing_coverage_is_unknown_on_gpu(cupy_device: int) -> None:
    vector_map = VectorMap(None, crs="EPSG:32618")
    index = GpuShorelineIndex(vector_map)
    points = ((0, 0), (100, -50))
    assert index.classify_candidates(points, device_id=cupy_device) == ("unknown", "unknown")
    assert index.visibility_many((0, 0), points, device_id=cupy_device) == (
        "unknown",
        "unknown",
    )
