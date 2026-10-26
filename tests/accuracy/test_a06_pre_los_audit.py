from __future__ import annotations

from datetime import UTC, datetime
from itertools import combinations
from math import atan2, degrees

import pytest
from pyproj import CRS, Geod, Transformer
from shapely.geometry import box

from accuracy.reference_assignment import enumerate_partial_assignments
from bearing_localization.search.config import SearchConfig
from bearing_localization.search.likelihood import build_likelihood_costs
from bearing_localization.search.pre_los_bound import pre_los_catalog_row_min_bound
from bearing_localization.search.pre_los_preparation import prepare_pre_los_targets
from bearing_localization.search.scoring import CandidateScorer
from maritime_localization_shared.contracts.inputs_v2 import (
    CatalogRecordV2,
    CatalogV2,
    MeasurementSetV2,
    MeasurementV2,
)
from maritime_localization_shared.geometry.angular_grouping import (
    DetectedObject,
    group_angular_detections,
)
from maritime_localization_shared.geospatial.vector_map import VectorMap

CRS_M = CRS.from_epsg(32618)
_ORIGIN = (-76.3, 37.0)
_TO_XY = Transformer.from_crs("EPSG:4326", CRS_M, always_xy=True)
_GEOD = Geod(ellps="WGS84")
_CANDIDATE_X, _CANDIDATE_Y = _TO_XY.transform(*_ORIGIN)
_CANDIDATE: tuple[float, float] = (float(_CANDIDATE_X), float(_CANDIDATE_Y))
_TIME = datetime(2026, 10, 26, tzinfo=UTC).isoformat()


def _scenario():
    specs = (
        ("a", 359.7, 1000.0, "vessel", None),
        ("b", 0.1, 1100.0, "physical_aid", 5.0),
        ("c", 0.5, 1200.0, "shoreline", 25.0),
        ("d", 90.0, 1500.0, "physical_aid", 0.0),
        ("e", 210.0, 1800.0, "vessel", 50.0),
    )
    records = []
    for key, bearing, distance, kind, uncertainty in specs:
        lon, lat, _ = _GEOD.fwd(*_ORIGIN, bearing, distance)
        metadata = () if uncertainty is None else (("position_uncertainty_m", uncertainty),)
        records.append(
            CatalogRecordV2(
                key=key,
                latitude_deg=lat,
                longitude_deg=lon,
                raw_type=kind,
                normalized_type=kind,
                metadata=metadata,
            )
        )
    catalog = CatalogV2(
        records=tuple(records),
        timestamp_utc=_TIME,
        source="a06-audit",
        catalog_version="1",
        catalog_id="a06-prelos-audit",
    )
    measurements = MeasurementSetV2(
        (
            MeasurementV2(359.9, _TIME, 0.0, "vessel"),
            MeasurementV2(0.4, _TIME, 0.3, "physical_aid"),
            MeasurementV2(90.0, _TIME, 1.1, "shoreline"),
        )
    )
    config = SearchConfig(
        max_range_m=4000.0,
        circular_merge_tolerance_deg=0.5,
        expected_unmatched_reports=1.0,
    )
    map_ = VectorMap(
        None,
        crs=CRS_M,
        coverage_geometry=box(
            _CANDIDATE[0] - 10_000,
            _CANDIDATE[1] - 10_000,
            _CANDIDATE[0] + 10_000,
            _CANDIDATE[1] + 10_000,
        ),
    )
    scorer = CandidateScorer(measurements, catalog, map_, config)
    prepared = prepare_pre_los_targets(scorer, _CANDIDATE)
    return measurements, config, scorer, prepared


def _exact_subset_score(measurements, config, records, visible_keys) -> float:
    by_key = {record.key: record for record in records}
    detections = [
        DetectedObject(
            key, by_key[key].object_type or "unknown", by_key[key].bearing_deg, by_key[key].range_m
        )
        for key in visible_keys
    ]
    groups = group_angular_detections(
        detections,
        config.circular_merge_tolerance_deg,
        report_type_labels=config.use_types,
    ).truth_groups
    predicted = []
    for member_keys in groups:
        representative_key = min(member_keys, key=lambda key: (by_key[key].range_m, key))
        representative = by_key[representative_key]
        uncertainty = max(
            (
                by_key[key].uncertainty_m
                for key in member_keys
                if by_key[key].uncertainty_m is not None
            ),
            default=0.0,
        )
        predicted.append(
            (
                representative.bearing_deg,
                representative.object_type,
                degrees(atan2(uncertainty, representative.range_m)),
            )
        )
    costs = build_likelihood_costs(
        measurements,
        [item[0] for item in predicted],
        [item[1] for item in predicted],
        config,
        [item[2] for item in predicted],
    )
    assignments = enumerate_partial_assignments(
        costs.matched_costs.tolist(),
        costs.report_clutter_costs.tolist(),
        costs.group_miss_costs.tolist(),
    )
    return float(costs.baseline_constant) + min(item.cost for item in assignments)


def test_prepared_bundle_matches_real_candidate_prediction_and_all_visibility_subsets():
    measurements, config, scorer, prepared = _scenario()
    assert prepared.matches_scorer(scorer)
    assert prepared.supported, prepared.reason
    assert tuple(r.key for r in prepared.records) == ("a", "b", "c", "d", "e")
    exact = scorer.evaluate(_CANDIDATE)
    assert exact.status == "scored"
    assert exact.predicted is not None and exact.costs is not None
    bound = pre_los_catalog_row_min_bound(measurements, prepared, config)
    assert bound.supported and bound.bound is not None, bound.reason
    assert bound.bound <= exact.hypothesis.score + 1e-12

    keys = tuple(r.key for r in prepared.records)
    for size in range(len(keys) + 1):
        for visible in combinations(keys, size):
            score = _exact_subset_score(measurements, config, prepared.records, visible)
            assert bound.bound <= score + 1e-12, (visible, bound.bound, score)


def test_lower_bound_edge_floors_match_direct_uncertainty_variants():
    measurements, config, _, prepared = _scenario()
    result = pre_los_catalog_row_min_bound(measurements, prepared, config)
    assert result.supported and result.bound is not None
    records = prepared.records
    assert set(result.component_keys) == {("a", "b", "c"), ("d",), ("e",)}
    assert len(result.edge_floor_matrix) == len(measurements.measurements)
    assert all(len(row) == len(records) for row in result.edge_floor_matrix)
    for j, record in enumerate(records):
        component = next(group for group in result.component_keys if record.key in group)
        known = {
            prepared_record.uncertainty_m
            for prepared_record in records
            if prepared_record.key in component and prepared_record.uncertainty_m is not None
        }
        values = (
            {0.0, *(float(v) for v in known)}
            if record.uncertainty_m is None
            else {float(v) for v in known if v >= record.uncertainty_m}
        )
        variants = [
            build_likelihood_costs(
                measurements,
                [record.bearing_deg],
                [record.object_type],
                config,
                [degrees(atan2(value, record.range_m))],
            ).matched_costs[:, 0]
            for value in values
        ]
        for i in range(len(measurements.measurements)):
            expected = min(float(vector[i]) for vector in variants)
            assert result.edge_floor_matrix[i][j] == pytest.approx(expected, abs=1e-8, rel=1e-10)


def test_reference_assignment_oracle_manual_costs_preserve_ties_and_forbidden_edges():
    assignments = enumerate_partial_assignments(
        ((0.0, float("inf")), (float("inf"), 0.0)),
        (2.0, 2.0),
        (1.0, 1.0),
    )
    assert len(assignments) == 4
    best = tuple(item for item in assignments if item.cost == 0.0)
    assert {item.matches for item in best} == {((0, 0), (1, 1))}
    tied = enumerate_partial_assignments(((0.0, 0.0), (0.0, 0.0)), (0.0, 0.0), (0.0, 0.0))
    assert len(tied) == 7 and {item.cost for item in tied} == {0.0}
    empty = enumerate_partial_assignments((), (), ())
    assert len(empty) == 1 and empty[0].cost == 0.0
    one_report = enumerate_partial_assignments(((0.0,),), (2.0,), (1.0,))
    assert {item.cost for item in one_report} == {0.0, 3.0}
