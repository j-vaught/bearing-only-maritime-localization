from datetime import UTC, datetime
from math import atan2, degrees

import pytest
from pyproj import CRS, Geod, Transformer
from shapely.geometry import box

from bearing_localization.search.config import SearchConfig
from bearing_localization.search.pre_los_preparation import (
    PreparedPreLOSTargets,
    prepare_pre_los_targets,
)
from bearing_localization.search.prediction import predict_visible_groups
from bearing_localization.search.scoring import CandidateScorer
from maritime_localization_shared.contracts.inputs_v2 import (
    CatalogRecordV2,
    CatalogV2,
    MeasurementSetV2,
    MeasurementV2,
)
from maritime_localization_shared.contracts.object_types import normalize_object_type
from maritime_localization_shared.geospatial.coordinates import geodesic_bearing_distance
from maritime_localization_shared.geospatial.vector_map import VectorMap

CRS_UTM = CRS.from_epsg(32618)
_GEOD = Geod(ellps="WGS84")
_TO_XY = Transformer.from_crs("EPSG:4326", CRS_UTM, always_xy=True)
_FROM_XY = Transformer.from_crs(CRS_UTM, "EPSG:4326", always_xy=True)
_TIME = datetime(2026, 10, 26, tzinfo=UTC).isoformat()


def _at(distance_m: float, bearing_deg: float, origin=(-76.3, 37.0)):
    lon, lat, _ = _GEOD.fwd(origin[0], origin[1], bearing_deg, distance_m)
    return float(lon), float(lat)


def _record(key, distance_m, bearing_deg, *, object_type="vessel", metadata=()):
    lon, lat = _at(distance_m, bearing_deg)
    return CatalogRecordV2(
        key=key,
        longitude_deg=lon,
        latitude_deg=lat,
        normalized_type=object_type,
        metadata=metadata,
    )


def _scenario(records, *, unknown=None, use_types=True):
    candidate = tuple(float(value) for value in _TO_XY.transform(-76.3, 37.0))
    catalog = CatalogV2(
        records=tuple(records),
        timestamp_utc=_TIME,
        source="test",
        catalog_version="1",
        catalog_id="pre-los-test",
    )
    vector_map = VectorMap(
        None,
        crs=CRS_UTM,
        coverage_geometry=box(
            candidate[0] - 20_000,
            candidate[1] - 20_000,
            candidate[0] + 20_000,
            candidate[1] + 20_000,
        ),
        unknown_geometry=unknown,
    )
    measurements = MeasurementSetV2((MeasurementV2(0.0, _TIME, 0.1, "vessel"),), trial_id="pre-los")
    config = SearchConfig(
        bounds_xy=(
            candidate[0] - 10_000,
            candidate[0] + 10_000,
            candidate[1] - 10_000,
            candidate[1] + 10_000,
        ),
        min_range_m=100.0,
        max_range_m=5_000.0,
        range_inclusive=True,
        circular_merge_tolerance_deg=0.1,
        use_types=use_types,
    )
    return (
        candidate,
        catalog,
        vector_map,
        CandidateScorer(measurements, catalog, vector_map, config),
    )


def test_complete_pre_los_targets_match_clear_prediction_members_with_offsets_and_wrap():
    catalog_records = (
        _record("north-a", 1_000.0, 359.96),
        _record("north-b", 1_100.0, 0.04, object_type="physical_aid"),
        _record(
            "offset",
            1_800.0,
            90.0,
            metadata=(
                ("visible_feature_offset_east_m", 80.0),
                ("visible_feature_offset_north_m", -25.0),
                ("position_uncertainty_m", 4.0),
            ),
        ),
        _record("too-near", 50.0, 180.0),
        _record("virtual", 500.0, 200.0, object_type="virtual_aid"),
        _record("too-far", 5_500.0, 300.0),
    )
    candidate, _, vector_map, scorer = _scenario(catalog_records)
    prepared = prepare_pre_los_targets(scorer, candidate)
    assert prepared.supported
    assert prepared.reason is None
    assert prepared.matches_scorer(scorer)
    assert [item.key for item in prepared.records] == ["north-a", "north-b", "offset"]
    assert prepared.coverage_certificate is not None
    assert prepared.coverage_map_context is not None
    assert prepared.coverage_certificate.matches_map(prepared.coverage_map_context)
    assert prepared.coverage_certificate.covers(
        candidate,
        [(item.key, item.target_map_xy_m) for item in prepared.records],
    )

    predicted = predict_visible_groups(
        candidate,
        scorer.catalog,
        observer_crs=CRS_UTM,
        visibility_radius_m=scorer.config.max_range_m,
        minimum_range_m=scorer.config.min_range_m,
        vector_map=vector_map,
        range_inclusive=scorer.config.range_inclusive,
        angular_resolution_deg=scorer.config.circular_merge_tolerance_deg,
        type_labels_enabled=scorer.config.use_types,
        prepared_context=scorer._prediction_context,
    )
    assert predicted.coverage_status == "known"
    predicted_keys = {key for group in predicted.groups for key in group.member_keys}
    assert predicted_keys == {record.key for record in prepared.records}
    source_by_key = {record.key: record for record in scorer.catalog.records}
    origin_lonlat = _FROM_XY.transform(*candidate)
    for item in prepared.records:
        source = source_by_key[item.key]
        target_lon, target_lat = source.longitude_deg, source.latitude_deg
        if item.offset_quality == "calibrated":
            metadata = dict(source.metadata)
            east = metadata["visible_feature_offset_east_m"]
            north = metadata["visible_feature_offset_north_m"]
            offset_bearing = degrees(atan2(east, north)) % 360.0
            target_lon, target_lat, _ = _GEOD.fwd(
                target_lon, target_lat, offset_bearing, (east**2 + north**2) ** 0.5
            )
        expected = geodesic_bearing_distance(
            (float(origin_lonlat[0]), float(origin_lonlat[1])),
            (float(target_lon), float(target_lat)),
        )
        assert item.bearing_deg == expected.true_bearing_deg
        assert item.range_m == expected.distance_m
        assert item.target_map_xy_m == tuple(
            float(value) for value in _TO_XY.transform(target_lon, target_lat)
        )
        assert item.object_type == normalize_object_type(
            source.normalized_type or source.raw_type, type_labels_enabled=True
        )
        expected_uncertainty = dict(source.metadata).get("position_uncertainty_m")
        assert item.uncertainty_m == expected_uncertainty


def test_unsupported_nearby_model_fails_closed_even_when_outside_actual_range():
    records = (
        _record("ordinary", 1_000.0, 0.0),
        _record(
            "offset-envelope",
            6_000.0,
            90.0,
            metadata=(
                ("visible_feature_offset_east_m", 2_000.0),
                ("visible_feature_offset_north_m", 0.0),
            ),
        ),
        _record("unsupported", 6_500.0, 180.0, metadata=(("feature_width_m", 1.0),)),
    )
    candidate, _, _, scorer = _scenario(records)
    prepared = prepare_pre_los_targets(scorer, candidate)
    assert not prepared.supported
    assert prepared.reason == "one or more nearby physical records have unsupported feature models"
    assert prepared.unresolved_model_keys == ("unsupported",)
    assert [record.key for record in prepared.records] == ["ordinary"]
    assert prepared.coverage_certificate is None


def test_coverage_unknown_or_map_context_failure_is_unresolved():
    candidate_xy = _TO_XY.transform(-76.3, 37.0)
    candidate, _, _, scorer = _scenario(
        (_record("target", 1_000.0, 0.0),),
        unknown=box(
            candidate_xy[0] - 100,
            candidate_xy[1] + 500,
            candidate_xy[0] + 100,
            candidate_xy[1] + 600,
        ),
    )
    prepared = prepare_pre_los_targets(scorer, candidate)
    assert not prepared.supported
    assert prepared.coverage_certificate is None
    assert prepared.reason is not None and "envelope" in prepared.reason


def test_land_and_unknown_observers_fail_closed():
    candidate, catalog, vector_map, _ = _scenario((_record("target", 1_000.0, 0.0),))
    land_map = VectorMap(
        box(candidate[0] - 10, candidate[1] - 10, candidate[0] + 10, candidate[1] + 10),
        crs=CRS_UTM,
        coverage_geometry=box(
            candidate[0] - 20_000,
            candidate[1] - 20_000,
            candidate[0] + 20_000,
            candidate[1] + 20_000,
        ),
    )
    scorer = CandidateScorer(
        MeasurementSetV2((MeasurementV2(0.0, _TIME, 0.1),), trial_id="land"),
        catalog,
        land_map,
        SearchConfig(max_range_m=5_000.0),
    )
    result = prepare_pre_los_targets(scorer, candidate)
    assert not result.supported
    assert result.reason == "candidate water status is land"
    assert [record.key for record in result.records] == ["target"]
    assert result.coverage_certificate is None


def test_bundle_rejects_forged_geometry_constructor_and_mismatched_scorer():
    candidate, _, _, scorer = _scenario((_record("target", 1_000.0, 0.0),))
    prepared = prepare_pre_los_targets(scorer, candidate)
    assert PreparedPreLOSTargets.__init__ is object.__init__
    with pytest.raises(AttributeError):
        setattr(prepared, "records", ())
    other_candidate, _, _, other_scorer = _scenario((_record("target", 1_000.0, 0.0),))
    assert not prepared.matches_scorer(other_scorer)
    assert prepared.candidate_xy_m == other_candidate


def test_duplicate_coordinates_empty_catalog_and_exact_range_boundaries():
    duplicate_a = _record("duplicate-a", 1_000.0, 12.0)
    duplicate_b = CatalogRecordV2(
        key="duplicate-b",
        longitude_deg=duplicate_a.longitude_deg,
        latitude_deg=duplicate_a.latitude_deg,
        normalized_type="vessel",
    )
    candidate, _, _, scorer = _scenario((duplicate_a, duplicate_b))
    prepared = prepare_pre_los_targets(scorer, candidate)
    assert prepared.supported
    assert [record.key for record in prepared.records] == ["duplicate-a", "duplicate-b"]

    empty_candidate, _, _, empty_scorer = _scenario(())
    empty = prepare_pre_los_targets(empty_scorer, empty_candidate)
    assert empty.supported
    assert empty.records == ()
    assert empty.coverage_certificate is not None

    boundary = _record("boundary", 1_000.0, 40.0)
    origin_lon, origin_lat = _FROM_XY.transform(*candidate)
    distance = geodesic_bearing_distance(
        (float(origin_lon), float(origin_lat)),
        (boundary.longitude_deg, boundary.latitude_deg),
    ).distance_m
    candidate, catalog, vector_map, _ = _scenario((boundary,))
    base_config = SearchConfig(
        bounds_xy=(
            candidate[0] - 10_000,
            candidate[0] + 10_000,
            candidate[1] - 10_000,
            candidate[1] + 10_000,
        ),
        min_range_m=distance,
        max_range_m=distance + 100.0,
        range_inclusive=True,
        use_types=True,
    )
    inclusive_scorer = CandidateScorer(
        MeasurementSetV2((MeasurementV2(0.0, _TIME, 0.1),), trial_id="inclusive"),
        catalog,
        vector_map,
        base_config,
    )
    inclusive = prepare_pre_los_targets(inclusive_scorer, candidate)
    assert inclusive.supported
    assert [record.key for record in inclusive.records] == ["boundary"]

    exclusive_config = SearchConfig(
        bounds_xy=base_config.bounds_xy,
        min_range_m=distance,
        max_range_m=distance + 100.0,
        range_inclusive=False,
        use_types=True,
    )
    exclusive_scorer = CandidateScorer(
        inclusive_scorer.measurements,
        catalog,
        vector_map,
        exclusive_config,
    )
    exclusive = prepare_pre_los_targets(exclusive_scorer, candidate)
    assert exclusive.supported
    assert exclusive.records == ()
