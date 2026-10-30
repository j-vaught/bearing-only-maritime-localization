from __future__ import annotations

import pytest
from pyproj import CRS, Geod, Transformer
from shapely.geometry import box

from maritime.geometry.vector_map import VectorMap
from maritime.schemas.inputs_v2 import CatalogRecordV2, CatalogV2
from maritime.search.deterministic_marker_scoring import (
    MarkerObservation,
    score_marker_candidates,
)
from maritime.search.prediction import (
    PredictedGroupSet,
    PredictedVisibleGroup,
    predict_visible_groups,
)

_CRS = CRS.from_epsg(32618)
_TO_XY = Transformer.from_crs("EPSG:4326", _CRS, always_xy=True)
_GEOD = Geod(ellps="WGS84")
_ORIGIN = (-76.3, 37.0)


def _catalog_record(
    key: str,
    bearing: float,
    *,
    range_m: float = 2_000.0,
    entity: str | None = None,
    object_type: str = "physical_aid",
    metadata: tuple[tuple[str, object], ...] = (),
) -> CatalogRecordV2:
    lon, lat, _ = _GEOD.fwd(*_ORIGIN, bearing, range_m)
    fields: dict[str, object] = {}
    if entity is not None:
        fields["physical_entity_id"] = entity
        fields["identity_ambiguous"] = False
        fields["physical_linkage_status"] = "resolved"
        fields["position_uncertainty_m"] = 0.0
        fields["visible_feature_offset_east_m"] = 0.0
        fields["visible_feature_offset_north_m"] = 0.0
    fields.update(dict(metadata))
    return CatalogRecordV2(
        key=key,
        latitude_deg=lat,
        longitude_deg=lon,
        normalized_type=object_type,
        metadata=tuple(fields.items()),
    )


def _catalog(*records: CatalogRecordV2) -> CatalogV2:
    return CatalogV2(
        records=tuple(records),
        timestamp_utc="2026-10-26T12:00:00Z",
        source="test",
        catalog_version="1",
        catalog_id="marker-score-test",
    )


def _group(
    key: str,
    bearing: float,
    *,
    range_m: float = 2_000.0,
    extent: float = 0.0,
    uncertainty: float | None = 0.0,
) -> PredictedVisibleGroup:
    return PredictedVisibleGroup(
        bearing_deg=bearing,
        object_type="physical_aid",
        member_keys=(key,),
        representative_key=key,
        range_m=range_m,
        extent_half_width_deg=extent,
        catalog_uncertainty_m=uncertainty,
    )


def _predicted(
    *groups: PredictedVisibleGroup, coverage: str = "known", unresolved=()
) -> PredictedGroupSet:
    return PredictedGroupSet(
        groups=tuple(groups),
        coverage_status=coverage,
        unresolved_member_keys=tuple(unresolved),
    )


def _score(markers, predicted, catalog, **kwargs):
    return score_marker_candidates(markers, predicted, catalog, **kwargs)


def test_missed_or_false_classified_markers_create_no_obligations():
    catalog = _catalog(_catalog_record("aid", 90, entity="aid-1"))
    predicted = _predicted(_group("aid", 90))

    empty = _score((), predicted, catalog)
    false_positive = _score((MarkerObservation("m0", 90, 1, positive=False),), predicted, catalog)
    assert empty.status == "consistent"
    assert false_positive.status == "consistent"
    assert false_positive.matches[0].outcome == "not_positive"


def test_positive_marker_requires_a_distinct_visible_physical_group_in_tolerance():
    catalog = _catalog(
        _catalog_record("aid-a", 40, entity="A"),
        _catalog_record("aid-b", 80, entity="B"),
    )
    predicted = _predicted(_group("aid-a", 40), _group("aid-b", 80))

    result = _score(
        (MarkerObservation("m1", 40.5, 1.0), MarkerObservation("m2", 80, 0.2)),
        predicted,
        catalog,
    )
    assert result.status == "consistent"
    assert {row.physical_entity_id for row in result.matches} == {"A", "B"}
    assert all(row.outcome == "matched" for row in result.matches)

    too_far = _score((MarkerObservation("m", 42, 1),), predicted, catalog, chart_complete=True)
    assert too_far.status == "inconsistent"
    assert too_far.matches[0].outcome == "no_compatible_group"


def test_angular_tolerance_wraps_across_zero_degrees():
    catalog = _catalog(_catalog_record("aid", 359.8, entity="A"))
    predicted = _predicted(_group("aid", 359.8))
    result = _score((MarkerObservation("north", 0.2, 0.5),), predicted, catalog)
    assert result.status == "consistent"
    assert result.matches[0].physical_entity_id == "A"


def test_duplicate_components_of_one_physical_aid_do_not_count_twice_or_reject():
    catalog = _catalog(
        _catalog_record("chart-a", 120, entity="same-aid"),
        _catalog_record("chart-b", 120.05, entity="same-aid"),
    )
    predicted = _predicted(
        PredictedVisibleGroup(
            bearing_deg=120.02,
            object_type="physical_aid",
            member_keys=("chart-a", "chart-b"),
            representative_key="chart-a",
            range_m=2_000,
            extent_half_width_deg=0.05,
            catalog_uncertainty_m=0.0,
        )
    )
    one = _score((MarkerObservation("m", 120, 0.2),), predicted, catalog)
    two = _score(
        (MarkerObservation("m1", 120, 0.2), MarkerObservation("m2", 120.02, 0.2)),
        predicted,
        catalog,
    )
    # A shared identity at different coordinates remains an alias ambiguity;
    # it cannot certify support or turn missing markers into a hard rejection.
    assert one.status == "unresolved"
    assert one.reason is not None
    assert two.status != "inconsistent"


def test_unresolved_chart_identity_cannot_cause_a_false_rejection():
    catalog = _catalog(
        _catalog_record(
            "ambiguous",
            25,
            metadata=(("identity_ambiguous", True), ("physical_linkage_status", "unresolved")),
        )
    )
    result = _score(
        (MarkerObservation("m", 25, 0.5),),
        _predicted(_group("ambiguous", 25)),
        catalog,
    )
    assert result.status == "unresolved"
    assert result.matches[0].outcome == "unresolved"


def test_candidate_land_unknown_chart_coverage_and_uncalibrated_tolerance_are_conservative():
    catalog = _catalog(_catalog_record("aid", 45, entity="A"))
    predicted = _predicted(_group("aid", 45))
    marker = MarkerObservation("m", 45, 0.5)

    on_land = _score((marker,), predicted, catalog, map_status="land")
    unknown_map = _score((marker,), predicted, catalog, map_status="unknown")
    unknown_chart = _score((marker,), _predicted(_group("aid", 45), coverage="unresolved"), catalog)
    uncalibrated = _score(
        (MarkerObservation("m", 45, 0.5, tolerance_calibrated=False),), predicted, catalog
    )
    assert on_land.status == "invalid_candidate"
    assert unknown_map.status == "unresolved"
    assert unknown_chart.status == "unresolved"
    assert uncalibrated.status == "unresolved"


def test_no_ais_vessels_and_no_top_n_truncation():
    # Vessels are not marker aids; the only matching aid intentionally appears
    # after many distractor groups to catch arbitrary top-N truncation.
    vessels = tuple(
        _catalog_record(f"v{i}", (i * 13) % 360, entity=f"vessel-{i}", object_type="vessel")
        for i in range(30)
    )
    aid = _catalog_record("far-in-list", 333, entity="aid")
    catalog = _catalog(*vessels, aid)
    groups = tuple(_group(record.key, (i * 13) % 360) for i, record in enumerate(vessels)) + (
        _group("far-in-list", 333),
    )
    result = _score((MarkerObservation("m", 333, 0.1),), _predicted(*groups), catalog)
    assert result.status == "consistent"
    assert result.matches[0].physical_entity_id == "aid"


def test_two_detections_cannot_both_claim_one_physical_group():
    catalog = _catalog(_catalog_record("aid", 15, entity="A"))
    result = _score(
        (MarkerObservation("m1", 15, 0.5), MarkerObservation("m2", 15.1, 0.5)),
        _predicted(_group("aid", 15)),
        catalog,
        chart_complete=True,
    )
    assert result.status == "inconsistent"


def test_vector_map_los_and_range_determine_the_visible_marker_groups():
    origin_xy = _TO_XY.transform(*_ORIGIN)
    coverage = box(
        origin_xy[0] - 10_000, origin_xy[1] - 10_000, origin_xy[0] + 10_000, origin_xy[1] + 10_000
    )
    land = box(origin_xy[0] - 80, origin_xy[1] + 900, origin_xy[0] + 80, origin_xy[1] + 1_100)
    vector_map = VectorMap(land, crs=_CRS, coverage_geometry=coverage)
    catalog = _catalog(
        _catalog_record("foreground", 0, range_m=700, entity="front"),
        _catalog_record("blocked", 0, range_m=1_500, entity="behind"),
        _catalog_record("out-of-range", 90, range_m=5_000, entity="far"),
    )
    predicted = predict_visible_groups(
        origin_xy,
        catalog,
        observer_crs=_CRS,
        visibility_radius_m=2_000,
        vector_map=vector_map,
    )
    result = _score((MarkerObservation("m", 0, 0.2),), predicted, catalog)
    assert tuple(group.representative_key for group in predicted.groups) == ("foreground",)
    assert result.status == "consistent"
    assert result.matches[0].physical_entity_id == "front"


def test_missing_predicted_member_key_stays_unresolved():
    catalog = _catalog()
    predicted = _predicted(_group("missing-key", 210))
    result = _score(
        (MarkerObservation("m", 210, 0.2),),
        predicted,
        catalog,
        chart_complete=True,
    )
    assert result.status == "unresolved"


def test_raw_buoy_type_is_recognized_as_a_physical_aid():
    lon, lat, _ = _GEOD.fwd(*_ORIGIN, 70, 2_000)
    record = CatalogRecordV2(
        key="buoy",
        latitude_deg=lat,
        longitude_deg=lon,
        raw_type="buoy",
        metadata=(
            ("physical_entity_id", "buoy-1"),
            ("physical_linkage_status", "resolved"),
            ("identity_ambiguous", False),
            ("position_uncertainty_m", 0.0),
            ("visible_feature_offset_east_m", 0.0),
            ("visible_feature_offset_north_m", 0.0),
        ),
    )
    result = _score(
        (MarkerObservation("m", 70, 0.2),),
        _predicted(_group("buoy", 70)),
        _catalog(record),
        catalog_object_identities_proven=True,
    )
    assert result.status == "consistent"
    assert result.matches[0].physical_entity_id == "buoy-1"


def test_non_unique_but_feasible_assignments_do_not_reject_candidate():
    catalog = _catalog(
        _catalog_record("aid-a", 100, entity="A"),
        _catalog_record("aid-b", 100.1, entity="B"),
    )
    predicted = _predicted(_group("aid-a", 100), _group("aid-b", 100.1))
    result = _score(
        (MarkerObservation("m1", 100.05, 0.2), MarkerObservation("m2", 100.05, 0.2)),
        predicted,
        catalog,
    )
    assert result.status == "consistent"
    assert result.association_ambiguous
    assert len(result.matches) == 2


def test_large_symmetric_matching_preserves_feasibility_without_top_n_cutoff():
    count = 24
    catalog = _catalog(
        *(
            _catalog_record(f"aid-{i}", 180, range_m=2000 + i, entity=f"entity-{i}")
            for i in range(count)
        )
    )
    predicted = _predicted(*(_group(f"aid-{i}", 180) for i in range(count)))
    markers = tuple(MarkerObservation(f"m-{i}", 180, 0.1) for i in range(count))
    result = _score(markers, predicted, catalog)
    assert result.status == "consistent"
    assert result.association_ambiguous
    assert len(result.matches) == count
    assert len({row.marker_id for row in result.matches}) == count


def test_unknown_position_uncertainty_cannot_be_ignored_by_nominal_bearing():
    catalog = _catalog(
        _catalog_record(
            "uncertain-position",
            250,
            entity="A",
            metadata=(("position_uncertainty_m", None),),
        )
    )
    result = _score(
        (MarkerObservation("m", 10, 0.1),),
        _predicted(_group("uncertain-position", 250)),
        catalog,
        chart_complete=True,
    )
    assert result.status == "unresolved"
    assert result.matches[0].outcome == "unresolved"
    assert result.matches[0].residual_deg is None
    assert result.total_abs_residual_deg is None


def test_vessel_only_chart_does_not_satisfy_a_physical_marker_observation():
    vessel = _catalog_record("ais-vessel", 45, entity="vessel-1", object_type="vessel")
    result = _score(
        (MarkerObservation("m", 45, 0.1),),
        _predicted(_group("ais-vessel", 45)),
        _catalog(vessel),
        chart_complete=True,
    )
    assert result.status == "inconsistent"
    assert result.matches[0].outcome == "no_compatible_group"


def test_tolerance_boundary_is_inclusive():
    catalog = _catalog(_catalog_record("aid", 45, entity="A"))
    result = _score(
        (MarkerObservation("m", 45.5, 0.5),),
        _predicted(_group("aid", 45)),
        catalog,
    )
    assert result.status == "consistent"
    assert result.matches[0].physical_entity_id == "A"


def test_absent_chart_match_is_unresolved_without_completeness_evidence():
    catalog = _catalog()
    result = _score(
        (MarkerObservation("m", 45, 0.5),),
        _predicted(),
        catalog,
    )
    assert result.status == "unresolved"
    assert result.matches[0].outcome == "unresolved"


def test_group_extent_can_match_away_from_representative_bearing():
    catalog = _catalog(_catalog_record("grouped-aid", 11, entity="A"))
    predicted = _predicted(_group("grouped-aid", 11, extent=2.0))

    result = _score(
        (MarkerObservation("m", 10, 0.1),),
        predicted,
        catalog,
        chart_complete=True,
    )

    assert result.status != "inconsistent"


def test_nonzero_position_uncertainty_keeps_nominally_out_of_range_aid_unresolved():
    record = _catalog_record(
        "uncertain-range",
        90,
        range_m=2_050,
        entity="A",
        metadata=(("position_uncertainty_m", 100.0),),
    )
    catalog = _catalog(record)
    origin_xy = _TO_XY.transform(*_ORIGIN)
    coverage = box(
        origin_xy[0] - 10_000,
        origin_xy[1] - 10_000,
        origin_xy[0] + 10_000,
        origin_xy[1] + 10_000,
    )
    vector_map = VectorMap(None, crs=_CRS, coverage_geometry=coverage)
    predicted = predict_visible_groups(
        origin_xy,
        catalog,
        observer_crs=_CRS,
        visibility_radius_m=2_000,
        vector_map=vector_map,
    )

    result = _score(
        (MarkerObservation("m", 90, 0.1),),
        predicted,
        catalog,
        chart_complete=True,
    )

    assert predicted.groups == ()
    assert result.status == "unresolved"


def test_nonzero_position_uncertainty_keeps_nominally_blocked_aid_unresolved():
    record = _catalog_record(
        "uncertain-los",
        0,
        range_m=1_500,
        entity="A",
        metadata=(("position_uncertainty_m", 100.0),),
    )
    catalog = _catalog(record)
    origin_xy = _TO_XY.transform(*_ORIGIN)
    coverage = box(
        origin_xy[0] - 10_000,
        origin_xy[1] - 10_000,
        origin_xy[0] + 10_000,
        origin_xy[1] + 10_000,
    )
    land = box(origin_xy[0] - 80, origin_xy[1] + 900, origin_xy[0] + 80, origin_xy[1] + 1_100)
    vector_map = VectorMap(land, crs=_CRS, coverage_geometry=coverage)
    predicted = predict_visible_groups(
        origin_xy,
        catalog,
        observer_crs=_CRS,
        visibility_radius_m=2_000,
        vector_map=vector_map,
    )

    result = _score(
        (MarkerObservation("m", 4, 0.1),),
        predicted,
        catalog,
        chart_complete=True,
    )

    assert predicted.groups == ()
    assert result.status == "unresolved"


def test_bare_entity_id_needs_caller_identity_proof_certificate():
    record = _catalog_record(
        "bare-id",
        30,
        entity=None,
        metadata=(
            ("physical_entity_id", "bare-entity"),
            ("position_uncertainty_m", 0.0),
            ("visible_feature_offset_east_m", 0.0),
            ("visible_feature_offset_north_m", 0.0),
        ),
    )
    catalog = _catalog(record)
    predicted = _predicted(_group("bare-id", 30))
    marker = (MarkerObservation("m", 30, 0.1),)

    safe_default = _score(marker, predicted, catalog)
    certified = _score(marker, predicted, catalog, catalog_object_identities_proven=True)

    assert safe_default.status == "unresolved"
    assert safe_default.total_abs_residual_deg is None
    assert certified.status == "consistent"
    assert certified.matches[0].physical_entity_id == "bare-entity"


def test_feasible_assignments_select_minimum_total_angular_residual_and_ignore_false_markers():
    catalog = _catalog(
        _catalog_record("aid-a", 10, entity="A"),
        _catalog_record("aid-b", 11, entity="B"),
    )
    predicted = _predicted(_group("aid-a", 10), _group("aid-b", 11))
    positives = (
        MarkerObservation("m1", 10, 1.0),
        MarkerObservation("m2", 10.6, 1.0),
    )

    scored = _score(positives, predicted, catalog)
    with_unobserved = _score(
        (*positives, MarkerObservation("unobserved", 200, 0.1, positive=False)),
        predicted,
        catalog,
    )

    assert scored.status == "consistent"
    assert {row.marker_id: row.physical_entity_id for row in scored.matches} == {
        "m1": "A",
        "m2": "B",
    }
    assert scored.total_abs_residual_deg == pytest.approx(0.4, abs=1e-9)
    assert {row.marker_id: row.residual_deg for row in scored.matches} == {
        "m1": pytest.approx(0.0, abs=1e-9),
        "m2": pytest.approx(0.4, abs=1e-9),
    }
    assert with_unobserved.total_abs_residual_deg == pytest.approx(
        scored.total_abs_residual_deg, abs=1e-12
    )
    assert {
        row.marker_id: row.physical_entity_id
        for row in with_unobserved.matches
        if row.outcome == "matched"
    } == {"m1": "A", "m2": "B"}
    assert (
        next(row for row in with_unobserved.matches if row.marker_id == "unobserved").outcome
        == "not_positive"
    )


def test_uncertainty_only_alternative_does_not_claim_an_exact_alternate_score():
    catalog = _catalog(
        _catalog_record("definite", 10, entity="A"),
        _catalog_record(
            "uncertain",
            10.1,
            metadata=(
                ("physical_entity_id", "B"),
                ("identity_ambiguous", True),
                ("physical_linkage_status", "unresolved"),
                ("position_uncertainty_m", None),
            ),
        ),
    )
    predicted = _predicted(_group("definite", 10), _group("uncertain", 10.1, uncertainty=None))

    result = _score((MarkerObservation("m", 10, 0.1),), predicted, catalog)

    assert result.status == "consistent"
    assert result.matches[0].physical_entity_id == "A"
    assert result.matches[0].residual_deg == pytest.approx(0.0)
    assert result.total_abs_residual_deg == pytest.approx(0.0)
    assert result.association_ambiguous
    assert result.alternative_assignment == ()
