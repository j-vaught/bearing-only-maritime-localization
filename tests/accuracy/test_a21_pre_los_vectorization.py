from __future__ import annotations

from math import atan2, degrees, fsum, inf, log, nextafter

import pytest

from bearing_localization.search import pre_los_bound as bound_module
from bearing_localization.search.config import SearchConfig
from bearing_localization.search.likelihood import build_likelihood_costs
from bearing_localization.search.pre_los_bound import pre_los_catalog_row_min_bound
from maritime_localization_shared.geometry.angular_grouping import (
    DetectedObject,
    group_angular_detections,
)

from .test_a21_pre_los import _measurements, _prepared_bundle, _records


def _scalar_reference(measurements, bundle, config, numerical_margin=1e-10):
    """Keep the former per-representative/per-uncertainty construction as an oracle."""
    records = tuple(bundle.records)
    detections = [
        DetectedObject(record.key, "unknown", record.bearing_deg, record.range_m)
        for record in records
    ]
    components = group_angular_detections(
        detections, config.circular_merge_tolerance_deg, report_type_labels=False
    ).truth_groups
    by_key = {record.key: record for record in records}
    empty = build_likelihood_costs(measurements, [], [], config)
    baseline = float(empty.baseline_constant)
    nr = len(measurements.measurements)
    clutter = (
        float(build_likelihood_costs(measurements, [0.0], [None], config).report_clutter_costs[0])
        if nr
        else -log(config.expected_unmatched_reports / 360.0)
    )
    columns = []
    for record in records:
        component = next(group for group in components if record.key in group)
        known = {
            by_key[key].uncertainty_m for key in component if by_key[key].uncertainty_m is not None
        }
        possible = (
            tuple(sorted({0.0, *(float(value) for value in known)}))
            if record.uncertainty_m is None
            else tuple(sorted(float(value) for value in known if value >= record.uncertainty_m))
        )
        variants = [
            build_likelihood_costs(
                measurements,
                [record.bearing_deg],
                [record.object_type],
                config,
                [degrees(atan2(value, record.range_m))],
            ).matched_costs[:, 0]
            for value in possible
        ]
        columns.append(tuple(float(min(variant[row] for variant in variants)) for row in range(nr)))
    edges = tuple(
        tuple(columns[column][row] for column in range(len(records))) for row in range(nr)
    )
    terms = tuple(min(clutter, min(row, default=inf)) for row in edges)
    raw = fsum((baseline, *terms))
    downward = numerical_margin * (1 + abs(baseline) + fsum(abs(value) for value in terms))
    result = nextafter(raw - downward, -inf)
    return edges, baseline, clutter, terms, raw, downward, result


def test_batched_bound_matches_scalar_cost_matrix_and_decomposition():
    config = SearchConfig(circular_merge_tolerance_deg=0.1)
    measurements = _measurements()
    bundle = _prepared_bundle(_records(), measurements, config)
    assert bundle.supported

    result = pre_los_catalog_row_min_bound(measurements, bundle, config)
    reference = _scalar_reference(measurements, bundle, config)

    assert result.supported
    assert result.edge_floor_matrix == reference[0]
    assert result.baseline_constant == reference[1]
    assert result.report_clutter_cost == reference[2]
    assert result.row_terms == reference[3]
    assert result.raw_row_min_bound == reference[4]
    assert result.applied_downward_margin == reference[5]
    assert result.bound == reference[6]


def test_likelihood_construction_is_one_empty_call_and_one_variant_batch(monkeypatch):
    config = SearchConfig(circular_merge_tolerance_deg=0.1)
    measurements = _measurements()
    bundle = _prepared_bundle(_records(), measurements, config)
    real_builder = bound_module.build_likelihood_costs
    calls = []

    def counted_builder(measurements_arg, predicted_bearings, *args, **kwargs):
        calls.append(len(predicted_bearings))
        return real_builder(measurements_arg, predicted_bearings, *args, **kwargs)

    monkeypatch.setattr(bound_module, "build_likelihood_costs", counted_builder)
    result = pre_los_catalog_row_min_bound(measurements, bundle, config)

    assert result.supported
    assert calls[0] == 0
    assert len(calls) == 2
    assert calls[1] > 1


@pytest.mark.parametrize("bearings,records", [((), ()), ((), _records()), ((0.02,), ())])
def test_empty_report_or_catalog_paths_keep_valid_bound_and_small_call_count(
    bearings, records, monkeypatch
):
    config = SearchConfig(circular_merge_tolerance_deg=0.1)
    measurements = _measurements(bearings)
    bundle = _prepared_bundle(records, measurements, config)
    assert bundle.supported

    real_builder = bound_module.build_likelihood_costs
    calls = []

    def counted_builder(measurements_arg, predicted_bearings, *args, **kwargs):
        calls.append(len(predicted_bearings))
        return real_builder(measurements_arg, predicted_bearings, *args, **kwargs)

    monkeypatch.setattr(bound_module, "build_likelihood_costs", counted_builder)
    result = pre_los_catalog_row_min_bound(measurements, bundle, config)

    assert result.supported
    assert result.bound is not None
    assert len(calls) == (2 if records and bearings else 1)
    assert calls[0] == 0
    if records and bearings:
        assert calls[1] >= len(records)
