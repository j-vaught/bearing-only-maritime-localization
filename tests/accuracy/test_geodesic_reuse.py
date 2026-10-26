from __future__ import annotations

import numpy as np
import pytest
from pyproj import CRS, Geod, Transformer
from shapely.geometry import box

import maritime.search.prediction as prediction_module
from maritime.geometry.spatial_index import GeographicSpatialIndex
from maritime.geometry.vector_map import VectorMap
from maritime.schemas.inputs_v2 import (
    CatalogRecordV2,
    CatalogV2,
    MeasurementSetV2,
    MeasurementV2,
)
from maritime.search.config import SearchConfig
from maritime.search.prediction import (
    predict_visible_groups,
    prepare_prediction_context,
)
from maritime.search.scoring import CandidateScorer

_GEOD = Geod(ellps="WGS84")
_CRS = CRS.from_epsg(32618)
_TO_XY = Transformer.from_crs("EPSG:4326", _CRS, always_xy=True)


def _record(
    key: str,
    origin: tuple[float, float],
    bearing: float,
    distance: float,
    metadata: tuple[tuple[str, object], ...] = (),
) -> CatalogRecordV2:
    longitude, latitude, _ = _GEOD.fwd(*origin, bearing, distance)
    return CatalogRecordV2(
        key=key,
        longitude_deg=float(longitude),
        latitude_deg=float(latitude),
        normalized_type="vessel",
        metadata=metadata,
    )


def _fixture():
    origin = (-76.3, 37.0)
    records = (
        _record("zero", origin, 359.95, 1_000.0),
        _record(
            "offset",
            origin,
            0.05,
            2_000.0,
            (
                ("visible_feature_offset_east_m", 12.0),
                ("visible_feature_offset_north_m", -4.0),
            ),
        ),
    )
    catalog = CatalogV2(
        records=records,
        timestamp_utc="2026-10-26T12:00:00Z",
        source="test",
        catalog_version="1",
        catalog_id="a13-geodesic-reuse",
    )
    east, north = _TO_XY.transform(*origin)
    observer = (float(east), float(north))
    vector_map = VectorMap(
        None,
        crs=_CRS,
        coverage_geometry=box(east - 20_000, north - 20_000, east + 20_000, north + 20_000),
    )
    context = prepare_prediction_context(
        catalog,
        observer_crs=_CRS,
        visibility_radius_m=10_000.0,
        vector_map=vector_map,
    )
    return origin, catalog, observer, vector_map, context


def test_radius_geometry_matches_legacy_indices_and_exact_inverse_values() -> None:
    origin = (-179.999, 70.0)
    points = (
        origin,
        (179.999, 70.0),
        (179.999, 70.0),
        (0.0, 0.0),
    )
    index = GeographicSpatialIndex(points, keys=("origin", "east", "duplicate", "far"))
    radius = 100_000.0
    geometries = index.query_radius_with_geometry(*origin, radius)
    assert tuple(row[0] for row in geometries) == index.query_radius(*origin, radius)
    assert tuple(row[0] for row in geometries) == (0, 1, 2)
    for index_position, bearing, distance in geometries:
        expected_bearing, _, expected_distance = _GEOD.inv(*origin, *points[index_position])
        assert bearing == float(expected_bearing) % 360.0
        assert distance == float(expected_distance)
        assert distance <= radius


def test_prediction_reuses_zero_offset_inverse_but_keeps_nonzero_offset_inverse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, catalog, observer, vector_map, context = _fixture()
    original = prediction_module.geodesic_bearing_distance
    calls: list[tuple[tuple[float, float], tuple[float, float]]] = []

    def counted(origin, target):
        calls.append((origin, target))
        return original(origin, target)

    monkeypatch.setattr(prediction_module, "geodesic_bearing_distance", counted)
    result = predict_visible_groups(
        observer,
        catalog,
        observer_crs=_CRS,
        visibility_radius_m=10_000.0,
        vector_map=vector_map,
        prepared_context=context,
    )
    assert len(calls) == 1
    assert result.coverage_status == "known"
    assert {key for group in result.groups for key in group.member_keys} == {"zero", "offset"}

    zero_record, offset_record = catalog.records
    expected_zero_bearing, _, expected_zero_range = _GEOD.inv(
        context.inverse.transform(*observer)[0],
        context.inverse.transform(*observer)[1],
        zero_record.longitude_deg,
        zero_record.latitude_deg,
    )
    zero_group = next(group for group in result.groups if "zero" in group.member_keys)
    assert zero_group.bearing_deg == pytest.approx(float(expected_zero_bearing) % 360.0)
    assert zero_group.range_m == pytest.approx(float(expected_zero_range))

    offset_bearing, offset_range = 108.43494882292201, (12.0**2 + 4.0**2) ** 0.5
    target_lon, target_lat, _ = _GEOD.fwd(
        offset_record.longitude_deg,
        offset_record.latitude_deg,
        offset_bearing,
        offset_range,
    )
    expected_offset_bearing, _, expected_offset_distance = _GEOD.inv(
        *context.inverse.transform(*observer), target_lon, target_lat
    )
    offset_group = next(group for group in result.groups if "offset" in group.member_keys)
    assert offset_group.bearing_deg == pytest.approx(float(expected_offset_bearing) % 360.0)
    assert offset_group.range_m == pytest.approx(float(expected_offset_distance))


def test_mixed_offset_candidate_score_matches_recomputed_inverse_geometry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, catalog, observer, vector_map, _ = _fixture()
    measurements = MeasurementSetV2(measurements=(MeasurementV2(0.0, "2026-10-26T12:00:00Z"),))
    config = SearchConfig(max_range_m=10_000.0)
    reused = CandidateScorer(measurements, catalog, vector_map, config).evaluate(observer)
    original_query = GeographicSpatialIndex.query_radius_with_geometry

    def recomputed_geometry(index, longitude_deg, latitude_deg, radius_m):
        rows = original_query(index, longitude_deg, latitude_deg, radius_m)
        result = []
        for record_index, _, _ in rows:
            record_lon, record_lat = index.points[record_index]
            geometry = prediction_module.geodesic_bearing_distance(
                (longitude_deg, latitude_deg), (record_lon, record_lat)
            )
            result.append((record_index, geometry.true_bearing_deg, geometry.distance_m))
        return tuple(result)

    monkeypatch.setattr(GeographicSpatialIndex, "query_radius_with_geometry", recomputed_geometry)
    recomputed = CandidateScorer(measurements, catalog, vector_map, config).evaluate(observer)

    assert reused.status == recomputed.status == "scored"
    assert reused.predicted == recomputed.predicted
    assert reused.costs is not None and recomputed.costs is not None
    assert np.array_equal(reused.costs.matched_costs, recomputed.costs.matched_costs)
    assert np.array_equal(reused.costs.report_clutter_costs, recomputed.costs.report_clutter_costs)
    assert np.array_equal(reused.costs.group_miss_costs, recomputed.costs.group_miss_costs)
    assert np.array_equal(reused.costs.residuals_deg, recomputed.costs.residuals_deg)
    assert reused.hypothesis == recomputed.hypothesis
