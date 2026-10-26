from __future__ import annotations

from math import log

import numpy as np
import pytest

from bearing_localization.search.config import SearchConfig
from bearing_localization.search.likelihood import build_likelihood_costs
from maritime_localization_shared.contracts.inputs_v2 import MeasurementSetV2, MeasurementV2


def measurement_set(*measurements: MeasurementV2) -> MeasurementSetV2:
    return MeasurementSetV2(tuple(measurements))


def m(bearing: float, sigma: float | None = None, kind: str | None = None) -> MeasurementV2:
    return MeasurementV2(bearing, "2026-10-25T12:00:00Z", sigma, kind, 30.0)


def test_primary_uses_each_report_sigma_and_ignores_extent() -> None:
    ms = measurement_set(m(10.0, 0.1), m(10.0, 2.0))
    costs = build_likelihood_costs(ms, [10.0], [None], SearchConfig())
    assert costs.matched_costs[0, 0] < costs.matched_costs[1, 0]
    changed_extent = measurement_set(MeasurementV2(10.0, "2026-10-25T12:00:00Z", 0.1, None, 0.0))
    same = build_likelihood_costs(changed_extent, [10.0], [None], SearchConfig())
    assert same.matched_costs[0, 0] == pytest.approx(costs.matched_costs[0, 0])


def test_legacy_formula_and_per_report_sigma() -> None:
    cfg = SearchConfig(objective_version="legacy_corrected_v0")
    costs = build_likelihood_costs(measurement_set(m(0, 0.5), m(0, 2.0)), [1], [None], cfg)
    assert costs.matched_costs[:, 0] == pytest.approx([2.0, 0.5])
    assert costs.report_clutter_costs == pytest.approx([24.0, 6.0])
    assert costs.group_miss_costs.tolist() == [0.0]
    assert costs.baseline_constant == 0.0


def test_wrapping_and_uniform_outlier_keep_remote_edge_finite() -> None:
    cfg = SearchConfig()
    costs = build_likelihood_costs([359.9], [0.1], [None], cfg, observation_sigmas_deg=[0.5])
    assert costs.residuals_deg[0, 0] == pytest.approx(0.2)
    remote = build_likelihood_costs([0.0], [180.0], [None], cfg)
    assert np.isfinite(remote.matched_costs[0, 0])
    assert remote.matched_costs[0, 0] > costs.matched_costs[0, 0]


def test_soft_type_zero_probability_forbids_edge_without_clipping() -> None:
    costs = build_likelihood_costs(
        [25.0],
        [25.0],
        [{"physical_aid": 1.0, "vessel": 0.0}],
        SearchConfig(),
        observation_types=["vessel"],
    )
    assert costs.matched_costs[0, 0] == float("inf")
    neutral = build_likelihood_costs(
        [25.0], [25.0], [None], SearchConfig(), observation_types=["unknown"]
    )
    plain = build_likelihood_costs([25.0], [25.0], [None], SearchConfig())
    assert neutral.matched_costs[0, 0] == pytest.approx(plain.matched_costs[0, 0])


def test_large_uncertainty_converges_to_uniform_density() -> None:
    costs = build_likelihood_costs(
        [0.0],
        [120.0],
        [None],
        SearchConfig(),
        predicted_uncertainty_deg=[1e6],
        observation_sigmas_deg=[1e6],
    )
    expected = -log(0.9 / 360.0)
    assert costs.matched_costs[0, 0] == pytest.approx(expected, abs=1e-10)


def test_cost_decomposition_and_negative_match_cost_are_preserved() -> None:
    costs = build_likelihood_costs(
        [0.0], [0.0], [None], SearchConfig(), observation_sigmas_deg=[0.05]
    )
    assert costs.matched_costs[0, 0] < 0.0
    assert costs.baseline_constant == 1.0
    assert costs.report_clutter_costs[0] == pytest.approx(-log(1 / 360))
    assert costs.group_miss_costs[0] == pytest.approx(-log(0.1))
    all_unmatched = costs.total_for_assignment([])
    matched = costs.total_for_assignment([(0, 0)])
    assert matched == pytest.approx(1 + costs.matched_costs[0, 0])
    assert all_unmatched == pytest.approx(
        1 + costs.report_clutter_costs[0] + costs.group_miss_costs[0]
    )
    assert matched < all_unmatched
    two_groups = build_likelihood_costs(
        [0.0],
        [0.0, 90.0],
        [None, None],
        SearchConfig(),
        observation_sigmas_deg=[0.05],
    )
    expected_one_miss = 1.0 + two_groups.matched_costs[0, 0] + two_groups.group_miss_costs[1]
    assert two_groups.total_for_assignment([(0, 0)]) == pytest.approx(expected_one_miss)


def test_invalid_inputs_and_unsupported_bias_are_rejected() -> None:
    with pytest.raises(ValueError, match="bearings"):
        build_likelihood_costs([360.0], [0.0], [None], SearchConfig())
    with pytest.raises(ValueError, match="uncertainty"):
        build_likelihood_costs([0.0], [0.0], [None], SearchConfig(), [-1.0])
    with pytest.raises(ValueError, match="sum to one"):
        build_likelihood_costs([0.0], [0.0], [{"vessel": 0.3}], SearchConfig())
    biased = MeasurementSetV2(
        (m(0.0),), calibrated_common_bias_mean_deg=1.0, calibrated_common_bias_sigma_deg=0.2
    )
    with pytest.raises(NotImplementedError, match="bias"):
        build_likelihood_costs(biased, [0.0], [None], SearchConfig())
    costs = build_likelihood_costs([0.0], [0.0], [None], SearchConfig())
    with pytest.raises(ValueError, match="injective"):
        costs.total_for_assignment([(0, 0), (0, 0)])


def test_diagonal_covariance_supplies_and_validates_per_report_sigma() -> None:
    diagonal = MeasurementSetV2(
        (m(0.0), m(0.0)),
        covariance_deg2=((0.01, 0.0), (0.0, 4.0)),
    )
    costs = build_likelihood_costs(diagonal, [0.0], [None], SearchConfig())
    explicit = build_likelihood_costs(
        measurement_set(m(0.0, 0.1), m(0.0, 2.0)),
        [0.0],
        [None],
        SearchConfig(),
    )
    assert costs.matched_costs[:, 0] == pytest.approx(explicit.matched_costs[:, 0])
    inconsistent = MeasurementSetV2(
        (m(0.0, 0.2),),
        covariance_deg2=((0.01,),),
    )
    with pytest.raises(ValueError, match="agree"):
        build_likelihood_costs(inconsistent, [0.0], [None], SearchConfig())


def test_non_diagonal_covariance_is_rejected_until_joint_likelihood() -> None:
    correlated = MeasurementSetV2(
        (m(0.0), m(10.0)),
        covariance_deg2=((1.0, 0.5), (0.5, 1.0)),
    )
    with pytest.raises(NotImplementedError, match="joint likelihood"):
        build_likelihood_costs(correlated, [0.0], [None], SearchConfig())
