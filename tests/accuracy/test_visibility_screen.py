from __future__ import annotations

from itertools import product

import pytest
from pyproj import CRS, Geod, Transformer
from shapely.geometry import box

from maritime.geometry.angular_grouping import (
    DetectedObject,
    group_angular_detections,
)
from maritime.geometry.vector_map import VectorMap
from maritime.schemas.inputs_v2 import CatalogRecordV2, CatalogV2
from maritime.search.prediction import (
    predict_visible_groups,
    prepare_prediction_context,
)
from maritime.search.visibility_screen import (
    _screen_candidate_visibility_batched,
    _screen_candidate_visibility_scalar,
    screen_candidate_visibility,
)

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


@pytest.mark.parametrize(
    "metadata",
    [
        (("source_feature_shift_m", 4.0),),
        (("target_offset_m", 4.0),),
        (("unrecognized_geometry_extension", 1.0),),
        (("source_attributes", {"length_m": 20.0}),),
        (
            ("source_attributes", {"objnam": "East Light"}),
            ("source_feature_id", "fid-row"),
            ("physical_linkage_status", "resolved"),
            ("identity_ambiguous", False),
        ),
    ],
)
def test_unknown_or_nested_geometry_model_metadata_prevents_certification(metadata):
    cat = catalog(record("model", 15, 500, metadata=metadata))
    result = screen_candidate_visibility(xy(), context(cat))
    if (
        metadata[0][0] == "source_attributes"
        and isinstance(metadata[0][1], dict)
        and "objnam" in metadata[0][1]
    ):
        assert result.certified
    else:
        assert not result.certified
        assert result.reason == "unsupported_feature_model"
        assert result.unresolved_model_member_keys == ("model",)


def test_supported_visible_feature_offset_metadata_remains_certifiable():
    cat = catalog(
        record(
            "offset",
            15,
            500,
            metadata=(
                ("visible_feature_offset_east_m", 4.0),
                ("visible_feature_offset_north_m", 3.0),
                ("position_uncertainty_m", 2.0),
                ("angular_half_width_deg", 0.02),
            ),
        )
    )
    result = screen_candidate_visibility(xy(), context(cat))
    assert result.certified
    assert result.minimum_visible_group_count == 1


def test_a34_resolved_component_reference_metadata_is_geometry_inert():
    metadata = (
        ("identity_ambiguous", False),
        ("identity_resolution", "same_chart_name_and_proximity"),
        ("observation_association_status", "candidate_geometry_only"),
        ("physical_entity_id", "physical-1"),
        ("physical_linkage_status", "resolved_by_chart_heuristic"),
        ("position_uncertainty_m", None),
        ("source_component_count", 1),
        (
            "source_components",
            [
                {
                    "feature_id": "feature-1",
                    "chart_id": "chart-1",
                    "scale_band": "general",
                    "service_layer": "Approach.Light_point",
                    "longitude_deg": -76.3,
                    "latitude_deg": 37.0,
                    "source_epoch_sordat": "20151104",
                }
            ],
        ),
        ("source_record_identity_status", "qualified_source_components"),
        ("uncertainty_status", "unspecified_by_source"),
    )
    cat = catalog(record("enc-resolved", 15, 500, metadata=metadata))
    result = screen_candidate_visibility(xy(), context(cat))
    assert result.certified
    assert result.minimum_visible_group_count == 1


def test_frozen_ais_motion_and_identity_metadata_is_allowed():
    metadata = (
        ("identity_kind", "source_key"),
        ("motion_status", "stale_extrapolation_disabled"),
        ("motion_valid", True),
        ("position_interpolated", False),
        ("position_uncertainty_m", None),
        ("uncertainty_status", "unspecified_by_source"),
    )
    cat = catalog(record("ais", 15, 500, metadata=metadata))
    result = screen_candidate_visibility(xy(), context(cat))
    assert result.certified
    assert result.minimum_visible_group_count == 1


def test_nonempty_nested_vertical_height_is_inert_in_the_2d_model():
    cat = catalog(
        record(
            "height",
            15,
            500,
            metadata=(("source_attributes", {"bcnshp": "stake", "height": "4.6"}),),
        )
    )
    result = screen_candidate_visibility(xy(), context(cat))
    assert result.certified
    assert result.minimum_visible_group_count == 1


def test_empty_nested_enc_height_does_not_claim_a_dimension_model():
    cat = catalog(
        record(
            "no-height",
            15,
            500,
            metadata=(("source_attributes", {"bcnshp": "stake", "height": ""}),),
        )
    )
    result = screen_candidate_visibility(xy(), context(cat))
    assert result.certified
    assert result.minimum_visible_group_count == 1


def test_vertical_height_and_a34_reference_metadata_match_scalar_prediction():
    metadata = (
        ("identity_ambiguous", False),
        ("identity_resolution", "same_chart_name_and_proximity"),
        ("observation_association_status", "candidate_geometry_only"),
        ("physical_entity_id", "physical-1"),
        ("physical_linkage_status", "resolved_by_chart_heuristic"),
        ("position_uncertainty_m", None),
        ("source_component_count", 1),
        (
            "source_components",
            [
                {
                    "feature_id": "feature-1",
                    "chart_id": "chart-1",
                    "scale_band": "general",
                    "service_layer": "Approach.Light_point",
                    "longitude_deg": -76.3,
                    "latitude_deg": 37.0,
                    "source_epoch_sordat": "20151104",
                }
            ],
        ),
        ("source_record_identity_status", "qualified_source_components"),
        ("source_attributes", {"height": "12.0", "objnam": "East Light"}),
        ("uncertainty_status", "unspecified_by_source"),
    )
    cat = catalog(record("resolved", 15, 500, metadata=metadata))
    ctx = context(cat)
    screen = screen_candidate_visibility(xy(), ctx, scan_all_members=True)
    prediction = predict_visible_groups(
        xy(),
        cat,
        observer_crs=CRS_UTM,
        visibility_radius_m=10_000,
        vector_map=ctx.vector_map,
        prepared_context=ctx,
    )
    assert screen.certified
    assert screen.exact_visible_group_count == len(prediction.groups)


def test_first_clear_member_certifies_component_without_raying_members_again():
    cat = catalog(record("near", 0, 100), record("same-bearing", 0.02, 200))
    result = screen_candidate_visibility(xy(), context(cat))
    assert result.certified and result.minimum_visible_group_count == 1
    assert result.ray_check_count == 1
    assert result.clear_count == 1
    assert result.complete is False
    assert result.components[0].confirmed_clear_key == "near"
    assert result.components[0].unexamined_member_keys == ("same-bearing",)


def test_full_scan_batch_matches_scalar_component_evidence_on_edge_cases():
    x, y = xy()
    target = GEOD.fwd(*ORIGIN, 0, 1000)
    tx, ty = XY.transform(target[0], target[1])
    cases = (
        (
            catalog(
                record("front", 0, 700),
                record("back", 0, 1500),
                record("north-west", 359.96, 900),
                record("north-east", 0.04, 950),
                record("solo", 30, 1200),
            ),
            vmap(land=box(x - 120, y + 900, x + 120, y + 1100)),
        ),
        (
            catalog(record("unknown-crossing", 0, 1000), record("clear", 20, 1200)),
            vmap(unknown=box(x - 100, y + 400, x + 100, y + 600)),
        ),
        (
            catalog(record("shore-endpoint", 0, 1000)),
            vmap(land=box(tx - 5, ty - 5, tx + 5, ty + 5)),
        ),
        (
            catalog(
                record(
                    "offset",
                    359.98,
                    800,
                    metadata=(
                        ("visible_feature_offset_east_m", 4.0),
                        ("visible_feature_offset_north_m", 3.0),
                        ("position_uncertainty_m", 2.0),
                        ("angular_half_width_deg", 0.02),
                    ),
                ),
                record(
                    "height",
                    0.02,
                    900,
                    metadata=(("source_attributes", {"height": "12.0", "objnam": "Light"}),),
                ),
            ),
            vmap(),
        ),
    )
    for cat, vector_map in cases:
        ctx = context(cat, vector_map)
        batch = _screen_candidate_visibility_batched(xy(), ctx, scan_all_members=True)
        scalar = _screen_candidate_visibility_scalar(xy(), ctx, scan_all_members=True)
        # Dataclass equality compares every result and per-component evidence field.
        assert batch == scalar


def test_batch_los_is_reserved_for_full_scans(monkeypatch):
    cat = catalog(record("north", 0, 100), record("east", 20, 200))
    ctx = context(cat)
    calls = []
    original = VectorMap.line_visibility_many

    def counted(self, observer_xy_m, targets_xy_m, **kwargs):
        calls.append(len(targets_xy_m))
        return original(self, observer_xy_m, targets_xy_m, **kwargs)

    monkeypatch.setattr(VectorMap, "line_visibility_many", counted)
    batched = _screen_candidate_visibility_batched(xy(), ctx, scan_all_members=True)
    assert batched.complete and calls == [2]
    calls.clear()
    screen_candidate_visibility(xy(), ctx, stop_after_minimum_groups=1)
    assert calls == []

    small = context(catalog(*(record(f"small-{i}", i * 3, 1000) for i in range(31))))
    screen_candidate_visibility(xy(), small, scan_all_members=True)
    assert calls == []
    threshold = context(catalog(*(record(f"threshold-{i}", i * 3, 1000) for i in range(32))))
    result = screen_candidate_visibility(xy(), threshold, scan_all_members=True)
    scalar = _screen_candidate_visibility_scalar(xy(), threshold, scan_all_members=True)
    assert result == scalar
    assert result.complete and calls == [32]
