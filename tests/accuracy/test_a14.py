from __future__ import annotations

import pytest
from pyproj import CRS, Geod, Transformer
from shapely.geometry import box

from bearing_localization.search.prediction import predict_visible_groups
from maritime_localization_shared.contracts.inputs_v2 import CatalogRecordV2, CatalogV2
from maritime_localization_shared.geospatial.vector_map import VectorMap

CRS_UTM = CRS.from_epsg(32618)
_GEOD = Geod(ellps="WGS84")
_TO_XY = Transformer.from_crs("EPSG:4326", CRS_UTM, always_xy=True)
_FROM_XY = Transformer.from_crs(CRS_UTM, "EPSG:4326", always_xy=True)


def _origin_xy(lon: float = -76.3, lat: float = 37.0) -> tuple[float, float]:
    east_m, north_m = _TO_XY.transform(lon, lat)
    return float(east_m), float(north_m)


def _at_distance(
    distance_m: float, bearing_deg: float, *, origin=(-76.3, 37.0)
) -> tuple[float, float]:
    lon, lat, _ = _GEOD.fwd(origin[0], origin[1], bearing_deg, distance_m)
    return float(lon), float(lat)


def _catalog(*records: CatalogRecordV2) -> CatalogV2:
    return CatalogV2(
        records=tuple(records),
        timestamp_utc="2026-10-25T12:00:00Z",
        source="test",
        catalog_version="1",
        catalog_id="a14-test",
    )


def _record(
    key: str,
    bearing_deg: float,
    range_m: float,
    object_type: str = "vessel",
    *,
    origin=(-76.3, 37.0),
    metadata: tuple[tuple[str, object], ...] = (),
) -> CatalogRecordV2:
    lon, lat = _at_distance(range_m, bearing_deg, origin=origin)
    return CatalogRecordV2(
        key=key,
        longitude_deg=lon,
        latitude_deg=lat,
        normalized_type=object_type,
        metadata=metadata,
    )


def _vector_map(*, land=None, unknown=None, coverage=None) -> VectorMap:
    if coverage is None:
        east_m, north_m = _origin_xy()
        coverage = box(east_m - 20_000, north_m - 20_000, east_m + 20_000, north_m + 20_000)
    return VectorMap(
        land,
        crs=CRS_UTM,
        coverage_geometry=coverage,
        unknown_geometry=unknown,
    )


def _predict(catalog: CatalogV2, origin=(-76.3, 37.0), **kwargs):
    return predict_visible_groups(
        _origin_xy(*origin),
        catalog,
        observer_crs=CRS_UTM,
        visibility_radius_m=10_000,
        vector_map=_vector_map(),
        **kwargs,
    )


def test_true_north_wraparound_merge_nearest_type_and_metadata() -> None:
    catalog = _catalog(
        _record("z-near-vessel", 359.96, 600, "vessel"),
        _record("a-far-aid", 0.04, 700, "physical_aid"),
        _record("virtual", 359.98, 500, "virtual_aid"),
        _record(
            "solo",
            30,
            900,
            "shoreline",
            metadata=(("position_uncertainty_m", 4.5), ("angular_half_width_deg", 0.02)),
        ),
    )

    result = _predict(catalog, angular_resolution_deg=0.1)

    assert len(result.groups) == 2
    north_group = next(group for group in result.groups if "z-near-vessel" in group.member_keys)
    assert north_group.member_keys == ("z-near-vessel", "a-far-aid")
    assert north_group.representative_key == "z-near-vessel"
    assert north_group.object_type == "vessel"
    assert north_group.range_m == pytest.approx(600.0, abs=1e-6)
    assert north_group.extent_half_width_deg == pytest.approx(0.08, abs=1e-6)
    solo = next(group for group in result.groups if group.representative_key == "solo")
    assert solo.catalog_uncertainty_m == 4.5
    assert solo.extent_half_width_deg == pytest.approx(0.02)
    assert not north_group.ais_antenna_offset_supported
    assert north_group.visible_feature_offset_quality == "nominal_zero_unknown"
    assert result.coverage_status == "known"


def test_type_label_suppression_does_not_restore_virtual_aids() -> None:
    catalog = _catalog(_record("physical", 10, 100), _record("virtual", 20, 100, "virtual_aid"))

    result = _predict(catalog, type_labels_enabled=False)

    assert tuple(group.representative_key for group in result.groups) == ("physical",)
    assert result.groups[0].object_type is None


def test_los_blocks_background_but_keeps_visible_foreground() -> None:
    origin_xy = _origin_xy()
    land = box(origin_xy[0] - 100, origin_xy[1] + 900, origin_xy[0] + 100, origin_xy[1] + 1_100)
    vector_map = _vector_map(land=land)
    catalog = _catalog(_record("foreground", 0, 700), _record("background", 0, 1_500))

    result = predict_visible_groups(
        origin_xy,
        catalog,
        observer_crs=CRS_UTM,
        visibility_radius_m=10_000,
        vector_map=vector_map,
    )

    assert tuple(group.representative_key for group in result.groups) == ("foreground",)
    assert result.coverage_status == "known"


def test_unknown_los_is_reported_unresolved_not_visible_or_blocked() -> None:
    origin_xy = _origin_xy()
    unknown = box(origin_xy[0] - 100, origin_xy[1] + 400, origin_xy[0] + 100, origin_xy[1] + 600)
    catalog = _catalog(_record("uncertain", 0, 1_000))
    vector_map = _vector_map(unknown=unknown)

    result = predict_visible_groups(
        origin_xy,
        catalog,
        observer_crs=CRS_UTM,
        visibility_radius_m=2_000,
        vector_map=vector_map,
    )

    assert result.groups == ()
    assert result.coverage_status == "unresolved"
    assert result.unresolved_member_keys == ("uncertain",)


def test_group_membership_changes_as_candidate_observer_moves() -> None:
    origin = (-76.3, 37.0)
    catalog = _catalog(
        _record("north", 0.0, 2_000, origin=origin),
        _record("slanted", 1.0, 2_000, origin=origin),
    )

    together = _predict(catalog, origin, angular_resolution_deg=1.5)
    moved_origin_xy = _origin_xy(*_at_distance(1_500, 0.0, origin=origin))
    separated = predict_visible_groups(
        moved_origin_xy,
        catalog,
        observer_crs=CRS_UTM,
        visibility_radius_m=10_000,
        vector_map=_vector_map(),
        angular_resolution_deg=1.5,
    )

    assert len(together.groups) == 1
    assert len(separated.groups) == 2


def test_geodesic_radius_is_inclusive_and_not_projected_distance() -> None:
    target_lon, target_lat = _at_distance(5_000, 87.0)
    catalog = _catalog(
        CatalogRecordV2(
            key="boundary",
            longitude_deg=target_lon,
            latitude_deg=target_lat,
            normalized_type="vessel",
        )
    )
    observer_xy = _origin_xy()
    observer_lon, observer_lat = _FROM_XY.transform(*observer_xy)
    _, _, exact_distance_m = _GEOD.inv(observer_lon, observer_lat, target_lon, target_lat)
    result = predict_visible_groups(
        observer_xy,
        catalog,
        observer_crs=CRS_UTM,
        visibility_radius_m=exact_distance_m,
        vector_map=_vector_map(),
    )
    target_xy = _TO_XY.transform(target_lon, target_lat)
    projected_distance = (
        (target_xy[0] - observer_xy[0]) ** 2 + (target_xy[1] - observer_xy[1]) ** 2
    ) ** 0.5

    assert len(result.groups) == 1
    assert result.groups[0].range_m == pytest.approx(exact_distance_m, abs=1e-9)
    assert projected_distance != pytest.approx(5_000, abs=1e-4)


def test_range_boundary_excludes_feature_outside_exact_geodesic_radius() -> None:
    catalog = _catalog(_record("outside", 90.0, 1_000.001))
    result = predict_visible_groups(
        _origin_xy(),
        catalog,
        observer_crs=CRS_UTM,
        visibility_radius_m=1_000,
        vector_map=_vector_map(),
    )

    assert result.groups == ()
    assert result.unresolved_member_keys == ()


def test_unknown_observer_coverage_is_unresolved_even_with_empty_catalog() -> None:
    origin_xy = _origin_xy()
    catalog = _catalog()
    outside_coverage = box(
        origin_xy[0] + 100_000,
        origin_xy[1] + 100_000,
        origin_xy[0] + 110_000,
        origin_xy[1] + 110_000,
    )

    result = predict_visible_groups(
        origin_xy,
        catalog,
        observer_crs=CRS_UTM,
        visibility_radius_m=1_000,
        vector_map=_vector_map(coverage=outside_coverage),
    )

    assert result.groups == ()
    assert result.coverage_status == "unresolved"
    assert result.unresolved_member_keys == ()
    assert not result.unresolved_model


def test_supported_visible_feature_offset_corrects_true_bearing_and_range() -> None:
    raw_lon, raw_lat = _at_distance(1_000, 0.0)
    target_lon, target_lat, _ = _GEOD.fwd(raw_lon, raw_lat, 90.0, 200.0)
    catalog = _catalog(
        CatalogRecordV2(
            key="offset-ais",
            longitude_deg=raw_lon,
            latitude_deg=raw_lat,
            normalized_type="vessel",
            metadata=(
                ("visible_feature_offset_east_m", 200.0),
                ("visible_feature_offset_north_m", 0.0),
                ("position_uncertainty_m", 3.0),
            ),
        )
    )

    result = _predict(catalog)
    observer_lon, observer_lat = _FROM_XY.transform(*_origin_xy())
    expected_bearing, _, expected_range = _GEOD.inv(
        observer_lon, observer_lat, target_lon, target_lat
    )

    assert len(result.groups) == 1
    group = result.groups[0]
    assert group.bearing_deg == pytest.approx(expected_bearing % 360.0, abs=1e-8)
    assert group.range_m == pytest.approx(expected_range, abs=1e-6)
    assert group.catalog_uncertainty_m == 3.0
    assert group.ais_antenna_offset_supported
    assert group.visible_feature_offset_quality == "calibrated"


def test_unsupported_antenna_and_dimension_metadata_marks_model_unresolved() -> None:
    catalog = _catalog(
        _record(
            "unsupported",
            10.0,
            500.0,
            metadata=(("antenna_offset_m", 2.0), ("vessel_length_m", 30.0)),
        )
    )

    result = _predict(catalog)

    assert result.groups == ()
    assert result.unresolved_model
    assert result.unresolved_model_keys == ("unsupported",)
