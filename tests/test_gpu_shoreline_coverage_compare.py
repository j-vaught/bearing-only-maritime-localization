"""GPU shoreline parity for polygonal coverage and overlapping unknown regions."""

from __future__ import annotations

import pytest
from shapely.geometry import Polygon, box

from maritime.geometry.vector_map import VectorMap
from maritime.search.gpu.shoreline import GpuShorelineIndex


@pytest.fixture
def coverage_pair() -> tuple[VectorMap, GpuShorelineIndex]:
    coverage = Polygon(
        [
            (0.0, 0.0),
            (20.0, 0.0),
            (20.0, 20.0),
            (12.0, 20.0),
            (12.0, 8.0),
            (8.0, 8.0),
            (8.0, 20.0),
            (0.0, 20.0),
            (0.0, 0.0),
        ]
    )
    land = Polygon(
        [(13.0, 9.0), (17.0, 9.0), (17.0, 17.0), (13.0, 17.0), (13.0, 9.0)],
        holes=[[(14.0, 11.0), (15.0, 11.0), (15.0, 15.0), (14.0, 15.0), (14.0, 11.0)]],
    )
    unknown = box(3.0, 10.0, 5.0, 12.0)
    vector_map = VectorMap(
        land,
        crs="EPSG:32618",
        coverage_geometry=coverage,
        unknown_geometry=unknown,
    )
    cp = pytest.importorskip("cupy")
    try:
        if cp.cuda.runtime.getDeviceCount() < 1:
            pytest.skip("No CUDA device is available.")
    except Exception as error:
        pytest.skip(f"CUDA runtime is unavailable: {error}")
    return vector_map, GpuShorelineIndex(vector_map)


def test_concave_coverage_unknown_land_and_hole_point_statuses(coverage_pair) -> None:
    vector_map, index = coverage_pair
    points = (
        (2.0, 2.0),
        (16.0, 10.0),
        (14.5, 13.0),
        (4.0, 11.0),
        (10.0, 15.0),
        (12.0, 15.0),
        (13.0, 10.0),
        (5.0, 11.0),
        (20.0, 10.0),
    )
    expected = tuple(vector_map.point_water_status(point) for point in points)
    assert expected == (
        "water",
        "land",
        "water",
        "unknown",
        "unknown",
        "water",
        "water",
        "unknown",
        "water",
    )
    assert index.classify_candidates(points) == expected
    assert index.point_cpu_fallback_count == 4


def test_los_parity_across_concave_coverage_unknown_holes_and_shorelines(coverage_pair) -> None:
    vector_map, index = coverage_pair
    rays = (
        ((2.0, 2.0), (6.0, 2.0)),  # Water only.
        ((14.2, 13.0), (14.8, 13.0)),  # Remains in the polygon hole.
        ((2.0, 11.0), (7.0, 11.0)),  # Crosses interior unknown coverage.
        ((2.0, 19.0), (18.0, 19.0)),  # Exits and reenters concave coverage.
        ((1.0, 11.0), (18.0, 11.0)),  # Land blocks despite earlier unknown.
        ((13.0, 9.5), (13.0, 16.5)),  # Follows a land boundary.
        ((1.0, 10.0), (3.0, 10.0)),  # Touches unknown only at the endpoint.
        ((2.0, 2.0), (-2.0, 2.0)),  # Ends outside coverage.
        ((12.5, 10.0), (16.0, 10.0)),  # Crosses land interior.
        ((14.5, 13.0), (16.0, 13.0)),  # Leaves the hole into land.
    )
    expected = tuple(vector_map.line_visibility(observer, target) for observer, target in rays)
    assert expected == (
        "clear",
        "clear",
        "unknown",
        "unknown",
        "blocked",
        "clear",
        "clear",
        "unknown",
        "blocked",
        "blocked",
    )
    for (observer, target), visibility in zip(rays, expected, strict=True):
        assert index.visibility_many(observer, (target,)) == (visibility,)
    assert index.visibility_gpu_count == len(rays)
    assert index.visibility_cpu_fallback_count == 3


@pytest.mark.parametrize(
    ("width_m", "expected"),
    ((0.5e-6, "unknown"), (1.0e-6, "unknown"), (1.5e-6, "blocked")),
)
def test_overlapping_unknown_does_not_change_land_overlap_tolerance(
    width_m: float, expected: str
) -> None:
    coverage = Polygon([(0.0, 0.0), (20.0, 0.0), (20.0, 10.0), (0.0, 10.0), (0.0, 0.0)])
    land = box(10.0, 2.0, 10.0 + width_m, 8.0)
    unknown = box(3.0, 4.0, 5.0, 6.0)
    vector_map = VectorMap(
        land,
        crs="EPSG:32618",
        coverage_geometry=coverage,
        unknown_geometry=unknown,
    )
    cp = pytest.importorskip("cupy")
    try:
        if cp.cuda.runtime.getDeviceCount() < 1:
            pytest.skip("No CUDA device is available.")
    except Exception as error:
        pytest.skip(f"CUDA runtime is unavailable: {error}")
    index = GpuShorelineIndex(vector_map)
    observer, target = (1.0, 5.0), (19.0, 5.0)
    assert vector_map.line_visibility(observer, target, tolerance_m=1e-6) == expected
    assert index.visibility_many(observer, (target,), tolerance_m=1e-6) == (expected,)


def test_unknown_overlapping_land_is_unknown_when_no_known_land_remains() -> None:
    coverage = box(0.0, 0.0, 20.0, 10.0)
    land = box(8.0, 2.0, 12.0, 8.0)
    unknown = box(8.0, 2.0, 12.0, 8.0)
    vector_map = VectorMap(
        land,
        crs="EPSG:32618",
        coverage_geometry=coverage,
        unknown_geometry=unknown,
    )
    cp = pytest.importorskip("cupy")
    try:
        if cp.cuda.runtime.getDeviceCount() < 1:
            pytest.skip("No CUDA device is available.")
    except Exception as error:
        pytest.skip(f"CUDA runtime is unavailable: {error}")
    index = GpuShorelineIndex(vector_map)
    observer, target = (6.0, 5.0), (14.0, 5.0)
    assert vector_map.line_visibility(observer, target) == "unknown"
    assert index.visibility_many(observer, (target,)) == ("unknown",)
