from __future__ import annotations

from datetime import UTC, datetime
from math import inf, isfinite

import pytest

from bearing_localization.search.candidate_lower_bound import (
    candidate_objective_lower_bound,
    should_prune_candidate,
)
from bearing_localization.search.config import SearchConfig
from bearing_localization.search.likelihood import build_likelihood_costs
from maritime_localization_shared.contracts.inputs_v2 import MeasurementSetV2, MeasurementV2

from .reference_assignment import enumerate_partial_assignments


def _oracle_score(costs) -> float:
    assignments = enumerate_partial_assignments(
        costs.matched_costs.tolist(),
        costs.report_clutter_costs.tolist(),
        costs.group_miss_costs.tolist(),
    )
    return float(costs.baseline_constant) + min(item.cost for item in assignments)


def _timestamps(count: int) -> tuple[str, ...]:
    base = datetime(2026, 10, 26, tzinfo=UTC)
    return tuple(base.isoformat() for _ in range(count))


@pytest.mark.parametrize(
    (
        "bearings",
        "report_sigmas",
        "report_types",
        "pred_bearings",
        "pred_types",
        "pred_sigmas",
        "k",
    ),
    [
        ([], [], [], [], [], [], 0),
        ([359.9], [0.0], ["vessel"], [0.1, 180.0], ["vessel", "physical_aid"], [0.0, 2.0], 1),
        (
            [359.8, 0.3],
            [0.05, 0.4],
            ["vessel", "physical_aid"],
            [0.2, 180.0, 45.0],
            [
                {"vessel": 0.55, "physical_aid": 0.45},
                {"vessel": 0.2, "physical_aid": 0.8},
                None,
            ],
            [0.0, 0.5, 3.0],
            1,
        ),
        (
            [0.0, 89.0, 270.0],
            [0.0, 0.05, 40.0],
            ["vessel", "physical_aid", "shoreline"],
            [0.0, 90.0, 180.0, 359.8],
            ["vessel", "shoreline", "physical_aid", None],
            [0.0, 1.0, 10.0, 0.0],
            3,
        ),
    ],
)
def test_bound_is_below_exhaustive_complete_objective(
    bearings,
    report_sigmas,
    report_types,
    pred_bearings,
    pred_types,
    pred_sigmas,
    k,
):
    config = SearchConfig(
        sigma_floor_deg=0.05,
        generated_sigma_deg=0.5,
        expected_unmatched_reports=1.0,
        detection_probability=0.9,
        bearing_outlier_probability=0.05,
        use_types=True,
    )
    result = candidate_objective_lower_bound(
        bearings,
        k,
        config,
        visibility_count_certified=True,
        observation_types=report_types,
        observation_sigmas_deg=report_sigmas,
    )
    assert result.supported and result.bound is not None
    actual = build_likelihood_costs(
        bearings,
        pred_bearings,
        pred_types,
        config,
        pred_sigmas,
        observation_types=report_types,
        observation_sigmas_deg=report_sigmas,
    )
    assert result.bound <= _oracle_score(actual) + 1e-12
    assert len(result.cardinality_bounds) == len(bearings) + 1
    assert len(result.per_report_match_floors) == len(bearings)
    assert all(isfinite(value) for value in result.per_report_match_floors)


def test_k_zero_and_actual_group_count_above_k_are_sound():
    config = SearchConfig()
    bearings = [12.0, 180.0]
    sigmas = [0.0, 0.2]
    predicted = [5.0, 88.0, 179.0, 301.0]
    actual = build_likelihood_costs(
        bearings,
        predicted,
        [None] * len(predicted),
        config,
        [0.0] * len(predicted),
        observation_sigmas_deg=sigmas,
    )
    for k in (0, 1, 2):
        result = candidate_objective_lower_bound(
            bearings, k, config, visibility_count_certified=True, observation_sigmas_deg=sigmas
        )
        assert result.supported and result.bound is not None
        assert result.bound <= _oracle_score(actual) + 1e-12
    # K=0 permits every report to match a distinct unexamined group.
    assert len(result.cardinality_bounds) == 3


def test_zero_clutter_intensity_still_bounds_the_all_matched_case():
    config = SearchConfig(expected_unmatched_reports=0.0)
    observations = [45.0, 225.0]
    result = candidate_objective_lower_bound(
        observations, 1, config, visibility_count_certified=True
    )
    assert result.supported and result.bound is not None
    costs = build_likelihood_costs(
        observations,
        observations,
        [None, None],
        config,
        [0.0, 0.0],
    )
    # With zero clutter intensity, only the cardinality-two assignment is finite.
    actual_score = (
        costs.baseline_constant
        + float(costs.matched_costs[0, 0])
        + float(costs.matched_costs[1, 1])
    )
    assert isfinite(actual_score)
    assert result.bound <= actual_score + 1e-12


def test_negative_zero_residual_floors_use_sigma_floor_and_q_at_most_one():
    config = SearchConfig(sigma_floor_deg=0.05, generated_sigma_deg=0.5)
    result = candidate_objective_lower_bound(
        [0.0, 180.0],
        2,
        config,
        visibility_count_certified=True,
        observation_types=["vessel", "physical_aid"],
        observation_sigmas_deg=[0.0, 0.05],
    )
    assert result.supported
    assert len(result.per_report_match_floors) == 2
    assert all(value < 0.0 for value in result.per_report_match_floors)
    # These floors set q_type=1, which is no smaller than any valid hard/soft type factor.
    assert result.bound is not None


def test_measurement_set_v2_and_diagonal_covariance_use_canonical_variance_rules():
    measurements = MeasurementSetV2(
        (
            MeasurementV2(359.9, _timestamps(1)[0], sigma_deg=None, object_type="vessel"),
            MeasurementV2(0.2, _timestamps(2)[1], sigma_deg=2.0, object_type="physical_aid"),
        ),
        covariance_deg2=((0.04, 0.0), (0.0, 4.0)),
    )
    result = candidate_objective_lower_bound(
        measurements, 1, SearchConfig(), visibility_count_certified=True
    )
    assert result.supported and result.bound is not None
    assert result.per_report_match_floors[0] < result.per_report_match_floors[1]


def test_uncertified_or_unsupported_variance_and_objective_return_unresolved():
    config = SearchConfig()
    unverified = candidate_objective_lower_bound([0.0], 1, config, visibility_count_certified=False)
    assert not unverified.supported and unverified.bound is None
    assert unverified.reason is not None
    assert "not certified" in unverified.reason

    omitted_certificate = candidate_objective_lower_bound([0.0], 1, config)
    assert not omitted_certificate.supported and omitted_certificate.bound is None
    assert omitted_certificate.reason is not None
    assert "not certified" in omitted_certificate.reason

    variance = candidate_objective_lower_bound(
        [0.0], 1, config, visibility_count_certified=True, variance_model_supported=False
    )
    assert not variance.supported and variance.bound is None
    assert variance.reason is not None
    assert "variance" in variance.reason

    legacy = candidate_objective_lower_bound(
        [0.0],
        1,
        SearchConfig(objective_version="legacy_corrected_v0"),
        visibility_count_certified=True,
    )
    assert not legacy.supported and legacy.bound is None

    correlated = MeasurementSetV2(
        (
            MeasurementV2(0.0, _timestamps(2)[0], sigma_deg=1.0),
            MeasurementV2(10.0, _timestamps(2)[1], sigma_deg=1.0),
        ),
        covariance_deg2=((1.0, 0.25), (0.25, 1.0)),
    )
    unsupported = candidate_objective_lower_bound(
        correlated, 1, config, visibility_count_certified=True
    )
    assert not unsupported.supported and unsupported.bound is None
    assert unsupported.reason is not None
    assert "joint likelihood" in unsupported.reason


def test_type_bias_disabled_for_global_minimum_does_not_change_actual_objective():
    config = SearchConfig(use_types=True)
    result = candidate_objective_lower_bound(
        [0.0],
        1,
        config,
        visibility_count_certified=True,
        observation_types=["vessel"],
        observation_sigmas_deg=[0.05],
    )
    hard = build_likelihood_costs(
        [0.0],
        [0.0],
        ["vessel"],
        config,
        observation_types=["vessel"],
        observation_sigmas_deg=[0.05],
    )
    soft = build_likelihood_costs(
        [0.0],
        [0.0],
        [{"vessel": 0.5, "physical_aid": 0.5}],
        config,
        observation_types=["vessel"],
        observation_sigmas_deg=[0.05],
    )
    assert result.supported and result.bound is not None
    assert result.per_report_match_floors[0] <= hard.matched_costs[0, 0]
    assert result.per_report_match_floors[0] <= soft.matched_costs[0, 0]


def test_pruning_is_strict_and_requires_supported_bound_and_feasible_score():
    result = candidate_objective_lower_bound(
        [0.0], 2, SearchConfig(), visibility_count_certified=True
    )
    assert result.supported and result.bound is not None
    assert not should_prune_candidate(result, result.bound - 1e-10)
    assert should_prune_candidate(result, result.bound - 1e-6, comparison_margin=1e-10)
    assert not should_prune_candidate(
        candidate_objective_lower_bound(
            [0.0],
            2,
            SearchConfig(),
            visibility_count_certified=True,
            variance_model_supported=False,
        ),
        -100.0,
    )
    with pytest.raises(ValueError, match="finite"):
        should_prune_candidate(result, inf)
