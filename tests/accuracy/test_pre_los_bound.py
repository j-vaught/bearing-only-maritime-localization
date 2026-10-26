from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from itertools import combinations
from math import atan2, degrees
from typing import cast

import pytest
from pyproj import Geod, Transformer
from shapely.geometry import box

from maritime.geometry.angular_grouping import (
    DetectedObject,
    group_angular_detections,
)
from maritime.geometry.vector_map import VectorMap
from maritime.schemas.inputs_v2 import (
    CatalogRecordV2,
    CatalogV2,
    MeasurementSetV2,
    MeasurementV2,
)
from maritime.search.candidate_lower_bound import candidate_objective_lower_bound
from maritime.search.config import SearchConfig
from maritime.search.likelihood import build_likelihood_costs
from maritime.search.pre_los_bound import pre_los_catalog_row_min_bound
from maritime.search.pre_los_preparation import (
    PreLOSTargetRecord,
    PreparedPreLOSTargets,
    prepare_pre_los_targets,
)
from maritime.search.scoring import CandidateScorer

from .reference_assignment import enumerate_partial_assignments

BASE_TIME = datetime(2026, 10, 26, tzinfo=UTC).isoformat()
ORIGIN_LONLAT = (-76.3, 37.0)
_GEOD = Geod(ellps="WGS84")
_TO_XY = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True)


def _measurements(bearings=(0.02, 70.0, 185.0)) -> MeasurementSetV2:
    types = ("physical_aid", "physical_aid", "shoreline")[: len(bearings)]
    sigmas = (0.0, 0.3, 1.1)[: len(bearings)]
    return MeasurementSetV2(
        tuple(
            MeasurementV2(bearing, BASE_TIME, sigma, kind)
            for bearing, sigma, kind in zip(bearings, sigmas, types, strict=True)
        ),
        trial_id="a21-prelos",
    )


def _records() -> tuple[PreLOSTargetRecord, ...]:
    specs = (
        ("a", 359.94, 5.0, "vessel", None),
        ("b", 0.02, 10.0, "physical_aid", 1.5),
        ("c", 0.11, 20.0, "shoreline", 0.25),
        ("d", 70.0, 18.0, "physical_aid", 0.0),
        ("e", 185.0, 25.0, "shoreline", None),
    )
    return tuple(
        PreLOSTargetRecord(
            key,
            bearing,
            distance,
            kind,
            uncertainty,
            (0.0, 0.0),
            "calibrated",
        )
        for key, bearing, distance, kind, uncertainty in specs
    )


def _prepared_bundle(
    records: Sequence[PreLOSTargetRecord],
    measurements: MeasurementSetV2,
    config: SearchConfig,
    *,
    unknown_geometry=None,
    land_geometry=None,
    force_unsupported_model: bool = False,
) -> PreparedPreLOSTargets:
    projected = _TO_XY.transform(*ORIGIN_LONLAT)
    candidate_xy = (float(projected[0]), float(projected[1]))
    catalog_records = []
    for target in records:
        longitude, latitude, _ = _GEOD.fwd(
            ORIGIN_LONLAT[0],
            ORIGIN_LONLAT[1],
            target.bearing_deg,
            target.range_m,
        )
        metadata = (
            (("position_uncertainty_m", target.uncertainty_m),)
            if target.uncertainty_m is not None
            else ()
        )
        if force_unsupported_model and not catalog_records:
            metadata += (("feature_width_m", 1.0),)
        catalog_records.append(
            CatalogRecordV2(
                key=target.key,
                longitude_deg=float(longitude),
                latitude_deg=float(latitude),
                normalized_type=target.object_type if isinstance(target.object_type, str) else None,
                metadata=metadata,
            )
        )
    catalog = CatalogV2(
        tuple(catalog_records),
        BASE_TIME,
        "a21-test-catalog",
        "1",
        "a21-test",
    )
    vector_map = VectorMap(
        land_geometry,
        crs="EPSG:32618",
        coverage_geometry=box(
            candidate_xy[0] - 1000,
            candidate_xy[1] - 1000,
            candidate_xy[0] + 1000,
            candidate_xy[1] + 1000,
        ),
        unknown_geometry=unknown_geometry,
    )
    scorer = CandidateScorer(measurements, catalog, vector_map, config)
    return prepare_pre_los_targets(scorer, candidate_xy)


def _actual_score(
    measurements: MeasurementSetV2,
    visible_keys: Sequence[str],
    records: Sequence[PreLOSTargetRecord],
    config: SearchConfig,
) -> float:
    by_key = {record.key: record for record in records}
    detections = [
        DetectedObject(key, "unknown", by_key[key].bearing_deg, by_key[key].range_m)
        for key in visible_keys
    ]
    groups = group_angular_detections(
        detections, config.circular_merge_tolerance_deg, report_type_labels=False
    ).truth_groups
    predicted_bearings = []
    predicted_types = []
    predicted_sigmas = []
    for member_keys in groups:
        representative_key = min(member_keys, key=lambda key: (by_key[key].range_m, key))
        representative = by_key[representative_key]
        uncertainty_values: list[float] = []
        for key in member_keys:
            uncertainty_value = by_key[key].uncertainty_m
            if uncertainty_value is not None:
                uncertainty_values.append(uncertainty_value)
        uncertainty = max(uncertainty_values, default=0.0)
        predicted_bearings.append(representative.bearing_deg)
        predicted_types.append(representative.object_type)
        predicted_sigmas.append(degrees(atan2(uncertainty, representative.range_m)))
    costs = build_likelihood_costs(
        measurements, predicted_bearings, predicted_types, config, predicted_sigmas
    )
    assignments = enumerate_partial_assignments(
        costs.matched_costs.tolist(),
        costs.report_clutter_costs.tolist(),
        costs.group_miss_costs.tolist(),
    )
    return float(costs.baseline_constant) + min(item.cost for item in assignments)


def test_exhaustive_all_visibility_subsets_keep_bound_below_complete_assignment():
    config = SearchConfig(circular_merge_tolerance_deg=0.1)
    measurements = _measurements()
    bundle = _prepared_bundle(_records(), measurements, config)
    assert bundle.supported
    assert bundle.matches_scorer(bundle._scorer)
    records = bundle.records
    result = pre_los_catalog_row_min_bound(measurements, bundle, config)
    assert result.supported and result.bound is not None
    assert result.reason is None
    assert result.raw_row_min_bound is not None
    assert result.applied_downward_margin is not None
    assert result.bound < result.raw_row_min_bound
    assert len(result.edge_floor_matrix) == len(measurements.measurements)
    assert set(result.component_keys) == {("a", "b", "c"), ("d",), ("e",)}
    assert any(edge < 0.0 for row in result.edge_floor_matrix for edge in row)

    for size in range(len(records) + 1):
        for visible in combinations((record.key for record in records), size):
            exact = _actual_score(measurements, visible, records, config)
            assert result.bound <= exact + 1e-12, (visible, result.bound, exact)

    global_floor = candidate_objective_lower_bound(
        measurements,
        0,
        config,
        visibility_count_certified=True,
        variance_model_supported=True,
    )
    assert global_floor.supported and global_floor.bound is not None
    assert global_floor.bound <= result.raw_row_min_bound + 1e-12


def test_row_min_exhaustion_covers_more_groups_than_reports_and_more_reports_than_groups():
    config = SearchConfig(circular_merge_tolerance_deg=0.1)
    records = _records()
    one_report = _measurements((70.0,))
    many_groups = _prepared_bundle(records, one_report, config)
    assert many_groups.supported
    result = pre_los_catalog_row_min_bound(one_report, many_groups, config)
    assert result.supported
    assert set(result.component_keys) == {("a", "b", "c"), ("d",), ("e",)}

    one_group_records = tuple(records[:3])
    three_reports = _measurements()
    one_group = _prepared_bundle(one_group_records, three_reports, config)
    assert one_group.supported
    result_one_group = pre_los_catalog_row_min_bound(three_reports, one_group, config)
    assert result_one_group.supported
    assert set(result_one_group.component_keys) == {("a", "b", "c")}


def test_duplicate_target_coordinate_tie_uses_key_and_uncertainty_floor_includes_members():
    config = SearchConfig(circular_merge_tolerance_deg=0.1)
    records = (
        PreLOSTargetRecord("b", 45.0, 10.0, "physical_aid", 0.2, (0.0, 0.0), "calibrated"),
        PreLOSTargetRecord("a", 45.0, 10.0, "vessel", 1.2, (0.0, 0.0), "calibrated"),
    )
    measurements = _measurements((45.0,))
    bundle = _prepared_bundle(records, measurements, config)
    assert bundle.supported
    result = pre_los_catalog_row_min_bound(measurements, bundle, config)
    assert result.supported
    assert set(result.component_keys) == {("a", "b")}
    sigma = degrees(atan2(1.2, 10.0))
    exact_canonical = build_likelihood_costs(
        measurements, [45.0], ["vessel"], config, [sigma]
    ).matched_costs[0, 0]
    assert result.edge_floor_matrix[0][0] <= exact_canonical + 1e-12


def test_bundle_candidate_measurement_config_and_map_binding_fail_closed():
    config = SearchConfig()
    measurements = _measurements((0.02,))
    bundle = _prepared_bundle(_records()[:2], measurements, config)
    assert bundle.supported
    assert pre_los_catalog_row_min_bound(measurements, bundle, config).supported

    assert not pre_los_catalog_row_min_bound(_measurements((0.02,)), bundle, config).supported
    assert not pre_los_catalog_row_min_bound(measurements, bundle, SearchConfig()).supported

    other_measurements = _measurements((70.0,))
    other_bundle = _prepared_bundle(_records()[:2], other_measurements, config)
    assert other_bundle.candidate_xy_m == bundle.candidate_xy_m
    assert not pre_los_catalog_row_min_bound(measurements, other_bundle, config).supported


@pytest.mark.parametrize(
    "config,measurements,records,unknown_geometry,force_unsupported_model",
    [
        (
            SearchConfig(expected_unmatched_reports=0.0),
            _measurements((0.02,)),
            _records(),
            None,
            False,
        ),
        (
            SearchConfig(common_bearing_bias_prior_deg=(0.0, 1.0)),
            _measurements((0.02,)),
            _records(),
            None,
            False,
        ),
        (
            SearchConfig(),
            _measurements((0.02,)),
            (_records()[0],),
            None,
            True,
        ),
        (
            SearchConfig(),
            _measurements((0.02,)),
            _records(),
            box(
                _TO_XY.transform(*ORIGIN_LONLAT)[0] - 1.0,
                _TO_XY.transform(*ORIGIN_LONLAT)[1] - 1.0,
                _TO_XY.transform(*ORIGIN_LONLAT)[0] + 1.0,
                _TO_XY.transform(*ORIGIN_LONLAT)[1] + 1.0,
            ),
            False,
        ),
    ],
)
def test_unsupported_objective_or_geometry_returns_no_bound(
    config, measurements, records, unknown_geometry, force_unsupported_model
):
    bundle = _prepared_bundle(
        records,
        measurements,
        config,
        unknown_geometry=unknown_geometry,
        force_unsupported_model=force_unsupported_model,
    )
    result = pre_los_catalog_row_min_bound(measurements, bundle, config)
    assert not result.supported and result.bound is None and result.reason


def test_non_diagonal_covariance_is_unresolved():
    records = _records()
    measurements = MeasurementSetV2(
        tuple(
            MeasurementV2(bearing, BASE_TIME, None, kind)
            for bearing, kind in ((0.02, "physical_aid"), (70.0, "physical_aid"))
        ),
        covariance_deg2=((1.0, 0.1), (0.1, 1.0)),
        trial_id="a21-cov",
    )
    config = SearchConfig()
    bundle = _prepared_bundle(records, measurements, config)
    result = pre_los_catalog_row_min_bound(measurements, bundle, config)
    assert not result.supported and result.bound is None


def test_invalid_boolean_geometry_values_are_rejected():
    with pytest.raises(ValueError):
        PreLOSTargetRecord("bad", True, 1.0, None, None, (0.0, 0.0), "calibrated")
    with pytest.raises(ValueError):
        PreLOSTargetRecord("bad", 1.0, 1.0, None, True, (0.0, 0.0), "calibrated")
    with pytest.raises(ValueError):
        PreLOSTargetRecord("bad", 1.0, 1.0, None, None, (True, 0.0), "calibrated")


def test_hard_cross_type_edges_remain_finite_and_soft_zero_mass_is_not_admitted():
    config = SearchConfig()
    measurements = _measurements((30.0,))
    costs = build_likelihood_costs(measurements, [30.0], ["shoreline"], config, [0.0])
    assert costs.matched_costs[0, 0] < float("inf")

    # Catalog prediction exposes normalized hard labels only. The row-min API
    # cannot receive a caller-fabricated soft distribution through its bundle.
    hard = PreLOSTargetRecord("hard", 30.0, 10.0, "shoreline", 0.0, (0.0, 0.0), "calibrated")
    bundle = _prepared_bundle((hard,), measurements, config)
    result = pre_los_catalog_row_min_bound(measurements, bundle, config)
    assert result.supported
    assert result.edge_floor_matrix[0][0] < float("inf")


def test_forged_raw_records_are_not_accepted_as_a_prepared_bundle():
    config = SearchConfig()
    measurements = _measurements((0.02,))
    result = pre_los_catalog_row_min_bound(
        measurements, cast(PreparedPreLOSTargets, _records()), config
    )
    assert not result.supported and result.bound is None
