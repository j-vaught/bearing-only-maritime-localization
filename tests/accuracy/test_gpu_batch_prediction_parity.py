"""CPU parity checks for batched CuPy prediction geometry and grouped outputs."""

from __future__ import annotations

try:
    import cupy as cp

    _CUDA_AVAILABLE = cp.cuda.runtime.getDeviceCount() > 0
except Exception:
    cp = None
    _CUDA_AVAILABLE = False
import numpy as np
import pytest
from pyproj import CRS, Geod, Transformer
from shapely.geometry import box

from maritime.geometry.vector_map import VectorMap
from maritime.schemas.inputs_v2 import CatalogRecordV2, CatalogV2
from maritime.search.gpu.batch_prediction import predict_candidates_cupy
from maritime.search.prediction import predict_visible_groups

pytestmark = pytest.mark.skipif(not _CUDA_AVAILABLE, reason="CuPy CUDA runtime required")

_GEOD = Geod(ellps="WGS84")
_CRS = CRS.from_epsg(32618)
_TO_XY = Transformer.from_crs("EPSG:4326", _CRS, always_xy=True)


def _point(origin: tuple[float, float], bearing: float, distance: float) -> tuple[float, float]:
    lon, lat, _ = _GEOD.fwd(origin[0], origin[1], bearing, distance)
    return float(lon), float(lat)


def _record(key: str, point: tuple[float, float], label: str, metadata=()) -> CatalogRecordV2:
    return CatalogRecordV2(
        key=key,
        longitude_deg=point[0],
        latitude_deg=point[1],
        normalized_type=label,
        metadata=tuple(metadata),
    )


def _assert_group_parity(actual, expected) -> None:
    assert actual.coverage_status == expected.coverage_status
    assert actual.unresolved_member_keys == expected.unresolved_member_keys
    assert actual.unresolved_model == expected.unresolved_model
    assert actual.unresolved_model_keys == expected.unresolved_model_keys
    assert actual.identity_limited_keys == expected.identity_limited_keys
    assert len(actual.groups) == len(expected.groups)
    for got, want in zip(actual.groups, expected.groups, strict=True):
        assert got.member_keys == want.member_keys
        assert got.representative_key == want.representative_key
        assert got.object_type == want.object_type
        assert got.visible_feature_offset_quality == want.visible_feature_offset_quality
        assert got.ais_antenna_offset_supported == want.ais_antenna_offset_supported
        assert got.bearing_deg == pytest.approx(want.bearing_deg, abs=2e-9)
        assert got.range_m == pytest.approx(want.range_m, abs=2e-6)
        assert got.extent_half_width_deg == pytest.approx(want.extent_half_width_deg, abs=2e-9)
        assert got.catalog_uncertainty_m == want.catalog_uncertainty_m


def test_batched_gpu_geometry_and_grouping_match_cpu_for_wrap_types_and_boundaries() -> None:
    origin = (-76.3, 37.0)
    points = [
        _point(origin, 359.97, 500.0),
        _point(origin, 0.02, 700.0),
        _point(origin, 90.0, 400.0),
        _point(origin, 180.0, 399.0),
        _point(origin, 270.0, 2_000.0),
        _point(origin, 45.0, 2_001.0),
    ]
    keys = (
        "wrap-near",
        "wrap-far",
        "minimum-edge",
        "below-minimum",
        "maximum-edge",
        "outside-maximum",
    )
    labels = ("vessel", "physical_aid", "vessel", "vessel", "buoy", "vessel")
    metadata = (
        (("position_uncertainty_m", 4.0), ("angular_half_width_deg", 0.03)),
        (("position_uncertainty_m", 8.0), ("angular_half_width_deg", 0.02)),
        (("position_uncertainty_m", 2.0),),
        (),
        (("position_uncertainty_m", 5.0),),
        (),
    )
    catalog = CatalogV2(
        records=tuple(
            _record(k, p, t, md) for k, p, t, md in zip(keys, points, labels, metadata, strict=True)
        ),
        timestamp_utc="2026-10-25T12:00:00Z",
        source="gpu-prediction-parity",
        catalog_version="1",
        catalog_id="gpu-prediction-parity",
    )
    candidates = np.asarray([origin, _point(origin, 225.0, 100.0)], dtype=float)
    xy = np.asarray([_TO_XY.transform(*point) for point in candidates])
    coverage = box(xy[0, 0] - 10_000, xy[0, 1] - 10_000, xy[0, 0] + 10_000, xy[0, 1] + 10_000)
    vector_map = VectorMap(None, crs=_CRS, coverage_geometry=coverage)
    visibility = np.ones((len(candidates), len(points)), dtype=np.uint8)
    visibility[1, 3] = 2  # This pair is outside the range for the shifted candidate.
    uncertainty = np.asarray([4.0, 8.0, 2.0, np.nan, 5.0, np.nan])
    widths = np.asarray([0.03, 0.02, 0.0, 0.0, 0.0, 0.0])
    result = predict_candidates_cupy(
        candidates,
        points,
        candidate_ids=("origin", "shifted"),
        object_keys=keys,
        object_types=labels,
        visibility_state=visibility,
        visibility_radius_m=2_000.0,
        minimum_range_m=400.0,
        range_inclusive=True,
        angular_resolution_deg=0.1,
        angular_half_width_deg=widths,
        catalog_uncertainty_m=uncertainty,
    )

    # Numerical oracle for every candidate-object pair is the CPU WGS84 inverse.
    candidate_grid = np.repeat(candidates, len(points), axis=0)
    target_grid = np.tile(np.asarray(points), (len(candidates), 1))
    cpu_azimuth, _, cpu_range = _GEOD.inv(
        candidate_grid[:, 0], candidate_grid[:, 1], target_grid[:, 0], target_grid[:, 1]
    )
    gpu_ranges = cp.asnumpy(result.ranges_m).reshape(-1)
    gpu_bearings = cp.asnumpy(result.bearings_deg).reshape(-1)
    gpu_valid = cp.asnumpy(result.converged).reshape(-1)
    assert gpu_valid.all()
    assert np.max(np.abs(gpu_ranges - cpu_range)) < 2e-6
    bearing_error = (gpu_bearings - cpu_azimuth + 180.0) % 360.0 - 180.0
    assert np.max(np.abs(bearing_error)) < 2e-9

    cpu_inside = (cpu_range >= 400.0) & (cpu_range <= 2_000.0)
    assert np.array_equal(cp.asnumpy(result.in_range).reshape(-1), cpu_inside)
    assert np.array_equal(cp.asnumpy(result.visibility_state), visibility)
    for index, candidate in enumerate(candidates):
        expected = predict_visible_groups(
            tuple(_TO_XY.transform(*candidate)),
            catalog,
            observer_crs=_CRS,
            visibility_radius_m=2_000.0,
            minimum_range_m=400.0,
            vector_map=vector_map,
            angular_resolution_deg=0.1,
        )
        _assert_group_parity(result.groups[index], expected)

    # Circular wraparound joins the close pair, selects the nearer representative,
    # and preserves the representative type plus group uncertainty and extent.
    wrap_group = next(
        group for group in result.groups[0].groups if "wrap-near" in group.member_keys
    )
    assert wrap_group.member_keys == ("wrap-near", "wrap-far")
    assert wrap_group.representative_key == "wrap-near"
    assert wrap_group.object_type == "vessel"
    assert wrap_group.catalog_uncertainty_m == 8.0
    assert wrap_group.extent_half_width_deg == pytest.approx(0.07, abs=2e-9)


def test_in_range_unknown_visibility_and_disabled_type_labels_are_explicit() -> None:
    origin = (-76.3, 37.0)
    target = _point(origin, 12.0, 800.0)
    result = predict_candidates_cupy(
        [origin],
        [target],
        candidate_ids=("candidate",),
        object_keys=("target",),
        object_types=("vessel",),
        visibility_state=np.asarray([[2]], dtype=np.uint8),
        visibility_radius_m=1_000.0,
        type_labels_enabled=False,
    )
    expected_bearing, _, expected_range = _GEOD.inv(*origin, *target)
    assert cp.asnumpy(result.converged).item()
    assert cp.asnumpy(result.unresolved).item()
    assert cp.asnumpy(result.ranges_m).item() == pytest.approx(expected_range, abs=2e-6)
    actual_bearing = cp.asnumpy(result.bearings_deg).item()
    assert (actual_bearing - expected_bearing + 180.0) % 360.0 - 180.0 == pytest.approx(
        0.0, abs=2e-9
    )
    assert result.groups[0].coverage_status == "unresolved"
    assert result.groups[0].unresolved_member_keys == ("target",)
    assert result.groups[0].groups == ()
