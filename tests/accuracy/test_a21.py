from __future__ import annotations

from itertools import product

import numpy as np
import pytest

from bearing_localization.search.bounds import (
    full_circle_edge_lower_bounds,
    lower_bound_from_edge_floors,
    rectangular_bearing_enclosures,
    rectangular_region_lower_bound,
)
from bearing_localization.search.config import SearchConfig
from bearing_localization.search.likelihood import LikelihoodCostMatrix, build_likelihood_costs

from .reference_assignment import enumerate_partial_assignments, optimal_assignments


def _costs(matched, reports, groups, baseline=0.0):
    matrix = np.asarray(matched, dtype=float)
    return LikelihoodCostMatrix(
        matrix,
        np.asarray(reports, dtype=float),
        np.asarray(groups, dtype=float),
        baseline,
        np.zeros_like(matrix),
    )


def test_rectangular_enclosures_include_containment_and_all_corners():
    bounds = (0.0, 4.0, -2.0, 2.0)
    landmarks = ((2.0, 0.0), (-1.0, 0.0), (0.0, -2.0), (4.0, 2.0))
    enclosures = rectangular_bearing_enclosures(bounds, landmarks)
    assert len(enclosures) == 4
    assert all(
        item.full_circle and item.start_deg == 0 and item.end_deg == 360 for item in enclosures
    )
    assert "contains landmark" in enclosures[0].reason
    assert "arc not derived" in enclosures[1].reason
    assert "contains landmark" in enclosures[2].reason
    assert "contains landmark" in enclosures[3].reason


def test_full_circle_edge_minima_bound_wrapped_bearings_and_unequal_variances():
    config = SearchConfig(
        bearing_outlier_probability=0.03,
        sigma_floor_deg=0.05,
        generated_sigma_deg=1.0,
    )
    observations = np.asarray([0.0, 181.0])
    sigmas = np.asarray([0.05, 4.0])
    floors = full_circle_edge_lower_bounds(
        observations,
        ["vessel", "physical_aid"],
        config,
        observation_sigmas_deg=sigmas,
        observation_types=["vessel", "physical_aid"],
    )
    assert np.any(floors < 0.0)
    bearings_to_check = [(359.0, 0.0), (0.0, 1.0), (181.0, 359.0)]
    bearings_to_check.extend(product(range(0, 360, 15), repeat=2))
    for group_bearings in bearings_to_check:
        actual = build_likelihood_costs(
            observations,
            group_bearings,
            ["vessel", "physical_aid"],
            config,
            observation_types=["vessel", "physical_aid"],
            observation_sigmas_deg=sigmas,
        ).matched_costs
        assert np.all(floors <= actual + 1e-12)


def test_full_circle_edge_floors_allow_different_report_and_group_counts():
    config = SearchConfig()
    observations = np.asarray([359.8, 180.0])
    floors = full_circle_edge_lower_bounds(
        observations,
        ["vessel"],
        config,
        observation_types=["vessel", "physical_aid"],
        observation_sigmas_deg=[0.2, 2.0],
    )
    assert floors.shape == (2, 1)
    actual = build_likelihood_costs(
        observations,
        [0.1],
        ["vessel"],
        config,
        observation_types=["vessel", "physical_aid"],
        observation_sigmas_deg=[0.2, 2.0],
    ).matched_costs
    assert np.all(floors <= actual)
    with pytest.raises(ValueError, match="one entry per group"):
        full_circle_edge_lower_bounds(observations, None, config)


def test_partial_assignment_bound_includes_negative_cost_and_all_miss_terms():
    actual = _costs([[-4.0, 3.0], [2.0, -3.0]], [5.0, -1.0], [4.0, 2.0], -0.5)
    floors = np.asarray([[-8.0, -2.0], [-1.0, -5.0]])
    result = lower_bound_from_edge_floors(actual, floors, numerical_margin=0.0)
    assignments = optimal_assignments(actual.matched_costs.tolist(), [5.0, -1.0], [4.0, 2.0])
    assert result.bound == pytest.approx(
        min(item.cost for item in optimal_assignments(floors.tolist(), [5.0, -1.0], [4.0, 2.0]))
        - 0.5
    )
    assert result.bound is not None
    assert result.row_min_bound is not None
    assert result.assignment_bound is not None
    assert result.row_min_bound <= result.assignment_bound
    for assignment in assignments:
        assert result.bound is not None
        assert result.bound <= assignment.cost - 0.5 + 1e-12


def test_unsupported_region_regimes_return_structured_unresolved():
    costs = _costs([[1.0]], [2.0], [3.0], 4.0)
    for option in ("fixed_visibility", "fixed_grouping", "fixed_variance"):
        kwargs = {option: False}
        result = lower_bound_from_edge_floors(costs, [[0.0]], **kwargs)
        assert result.bound is None
        assert result.row_min_bound is None
        assert result.assignment_bound is None
        assert result.reason


def test_region_wrapper_checks_landmark_count_and_keeps_bounds_callable():
    costs = _costs([[2.0]], [1.0], [3.0], 4.0)
    result = rectangular_region_lower_bound(
        (0.0, 1.0, 0.0, 1.0),
        ((0.5, 0.5),),
        costs,
        [[0.0]],
    )
    assert result.bound is not None
    assert result.bound <= 4.0 + 1.0 + 3.0
    with pytest.raises(ValueError, match="Landmark count"):
        rectangular_region_lower_bound((0.0, 1.0, 0.0, 1.0), (), costs, [[0.0]])


def test_exhaustive_small_partial_assignments_respect_row_and_assignment_bounds():
    # Exhaust every 2x2 actual edge matrix over {-2,0,2}, every edge floor
    # obtained by subtracting 0 or 2, all miss vectors over {-1,0,1}, and
    # baselines over {-1,0,1}. For each region, compare against all injective
    # partial assignments, including ties and negative scores.
    values = (-2.0, 0.0, 2.0)
    miss_values = (-1.0, 0.0, 1.0)
    checked = 0
    for actual_tuple in product(values, repeat=4):
        actual = np.asarray(actual_tuple).reshape(2, 2)
        for decrement_tuple in product((0.0, 2.0), repeat=4):
            floors = actual - np.asarray(decrement_tuple).reshape(2, 2)
            for report_miss in product(miss_values, repeat=2):
                for group_miss in product(miss_values, repeat=2):
                    for baseline in miss_values:
                        cost = _costs(actual, report_miss, group_miss, baseline)
                        result = lower_bound_from_edge_floors(cost, floors, numerical_margin=0.0)
                        all_scores = [
                            assignment.cost + baseline
                            for assignment in enumerate_partial_assignments(
                                actual.tolist(), report_miss, group_miss
                            )
                        ]
                        assert result.row_min_bound is not None
                        assert result.assignment_bound is not None
                        assert result.row_min_bound <= min(all_scores) + 1e-12
                        assert result.assignment_bound <= min(all_scores) + 1e-12
                        checked += len(all_scores)
    assert checked >= 1_000_000


def test_seeded_random_edge_floors_are_supplementary_soundness_check():
    rng = np.random.default_rng(21021)
    for _ in range(250):
        actual = rng.normal(0.0, 5.0, size=(3, 2))
        floors = actual - rng.uniform(0.0, 3.0, size=(3, 2))
        reports = rng.normal(0.0, 3.0, size=3)
        groups = rng.normal(0.0, 3.0, size=2)
        baseline = float(rng.normal())
        result = lower_bound_from_edge_floors(
            _costs(actual, reports, groups, baseline), floors, numerical_margin=1e-10
        )
        optimum = min(
            assignment.cost + baseline
            for assignment in enumerate_partial_assignments(
                actual.tolist(), reports.tolist(), groups.tolist()
            )
        )
        assert result.bound is not None
        assert result.bound <= optimum + 1e-10
