from __future__ import annotations

from itertools import product

import pytest
from pyproj import CRS, Geod, Transformer
from shapely.geometry import box

from bearing_localization.search.prediction import (
    predict_visible_groups,
    prepare_prediction_context,
)
from bearing_localization.search.visibility_screen import screen_candidate_visibility
from maritime_localization_shared.contracts.inputs_v2 import CatalogRecordV2, CatalogV2
from maritime_localization_shared.geometry.angular_grouping import (
    DetectedObject,
    group_angular_detections,
)
from maritime_localization_shared.geospatial.vector_map import VectorMap

CRS_UTM = CRS.from_epsg(32618)
GEOD = Geod(ellps="WGS84")
XY = Transformer.from_crs("EPSG:4326", CRS_UTM, always_xy=True)
ORIGIN = (-76.3, 37.0)


def xy():
    return tuple(map(float, XY.transform(*ORIGIN)))


def record(key, bearing, distance, *, metadata=()):
    lon, lat, _ = GEOD.fwd(*ORIGIN, bearing, distance)
    return CatalogRecordV2(
        key=key, longitude_deg=lon, latitude_deg=lat, normalized_type="vessel", metadata=metadata
    )


def catalog(*records):
    return CatalogV2(
        records=tuple(records),
        timestamp_utc="2026-10-25T12:00:00Z",
        source="test",
        catalog_version="1",
        catalog_id="screen-test",
    )


def vmap(*, land=None, unknown=None):
    x, y = xy()
    return VectorMap(
        land,
        crs=CRS_UTM,
        coverage_geometry=box(x - 20_000, y - 20_000, x + 20_000, y + 20_000),
        unknown_geometry=unknown,
    )


def context(cat, vm=None, radius=10_000):
    return prepare_prediction_context(
        cat, observer_crs=CRS_UTM, visibility_radius_m=radius, vector_map=vm or vmap()
    )


def test_every_occlusion_subset_preserves_component_lower_bound():
    items = [
        DetectedObject("n", "vessel", 359.96, 10),
        DetectedObject("a", "vessel", 0.02, 11),
        DetectedObject("b", "vessel", 0.10, 12),
        DetectedObject("dup1", "vessel", 0.11, 13),
        DetectedObject("dup2", "vessel", 0.11, 14),
        DetectedObject("far", "vessel", 20, 15),
    ]
    original = group_angular_detections(items, 0.1).truth_groups
    for mask in product((False, True), repeat=len(items)):
        subset = [item for item, keep in zip(items, mask, strict=True) if keep]
        ids = {item.object_id for item in subset}
        witnessed = sum(bool(ids.intersection(group)) for group in original)
        assert len(group_angular_detections(subset, 0.1).truth_groups) >= witnessed


def test_full_scan_parity_and_group_bound():
    x, y = xy()
    land = box(x - 120, y + 900, x + 120, y + 1100)
    cat = catalog(
        record("front", 0, 700),
        record("back", 0, 1500),
        record("nw", 359.96, 900),
        record("ne", 0.04, 950),
        record("solo", 30, 1200),
    )
    vm = vmap(land=land)
    ctx = context(cat, vm)
    result = screen_candidate_visibility(xy(), ctx, scan_all_members=True)
    predicted = predict_visible_groups(
        xy(),
        cat,
        observer_crs=CRS_UTM,
        visibility_radius_m=10_000,
        vector_map=vm,
        prepared_context=ctx,
    )
    assert result.certified and result.complete
    assert result.exact_visible_group_count == len(predicted.groups)
    assert result.minimum_visible_group_count <= len(predicted.groups)
    assert result.blocked_count >= 1 and result.clear_count >= 1


def test_early_stop_is_certified_but_incomplete():
    cat = catalog(
        record("n1", 0, 100),
        record("n2", 0.02, 200),
        record("east", 20, 300),
        record("north", 40, 400),
    )
    result = screen_candidate_visibility(xy(), context(cat), stop_after_minimum_groups=2)
    assert result.certified and result.minimum_visible_group_count == 2
    assert not result.complete and result.exact_visible_group_count is None
    assert result.unexamined_component_count == 1
    assert result.ray_check_count >= 2


def test_unknown_does_not_confirm_and_invalidates_certificate():
    x, y = xy()
    unknown = box(x - 100, y + 400, x + 100, y + 600)
    cat = catalog(record("unknown", 0, 1000), record("clear", 20, 1200))
    result = screen_candidate_visibility(xy(), context(cat, vmap(unknown=unknown)))
    assert not result.certified and result.reason == "unknown_line_of_sight"
    assert result.minimum_visible_group_count == 1
    assert result.unknown_count == 1 and result.clear_count == 1
    assert result.unresolved_coverage_member_keys == ("unknown",)


def test_unsupported_model_invalidates_certificate():
    cat = catalog(record("dimensioned", 0, 500, metadata=(("length_m", 10.0),)))
    result = screen_candidate_visibility(xy(), context(cat))
    assert not result.certified and result.reason == "unsupported_feature_model"
    assert result.unresolved_model_member_keys == ("dimensioned",)


def test_empty_catalog_with_unknown_observer_is_unresolved():
    x, y = xy()
    vm = VectorMap(
        None, crs=CRS_UTM, coverage_geometry=box(x + 10_000, y + 10_000, x + 20_000, y + 20_000)
    )
    result = screen_candidate_visibility(xy(), context(catalog(), vm))
    assert result.minimum_visible_group_count == 0 and not result.certified
    assert result.reason == "unknown_observer_coverage"


def test_inclusive_shoreline_endpoint_delegates_exact_los_to_map():
    cat = catalog(record("endpoint", 0, 1000))
    target = GEOD.fwd(*ORIGIN, 0, 1000)
    tx, ty = XY.transform(target[0], target[1])
    vm = vmap(land=box(tx - 5, ty - 5, tx + 5, ty + 5))
    result = screen_candidate_visibility(xy(), context(cat, vm, radius=1001), scan_all_members=True)
    assert result.complete and result.ray_check_count == 1
    assert result.blocked_count == 1 or result.clear_count == 1


def test_invalid_stop_count_rejected():
    with pytest.raises(ValueError):
        screen_candidate_visibility(xy(), context(catalog()), stop_after_minimum_groups=-1)
