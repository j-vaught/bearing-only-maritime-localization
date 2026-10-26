"""A29 assignment acceleration gate and exact CPU fallback checks."""

from __future__ import annotations

import numpy as np
import pytest

from bearing_localization.search.assignment import (
    assign_likelihood_costs,
    assign_partial_cost_matrix,
)
from bearing_localization.search.gpu.assignment import assignment_acceleration_decision
from bearing_localization.search.likelihood import LikelihoodCostMatrix

from .reference_assignment import optimal_assignments


def test_prototype_gate_defers_below_twenty_percent_and_opens_at_threshold() -> None:
    assert assignment_acceleration_decision(0.199999) == "defer_cpu"
    assert assignment_acceleration_decision(0.20) == "prototype_eligible"
    assert assignment_acceleration_decision(0.8) == "prototype_eligible"


@pytest.mark.parametrize("fraction", [-0.01, 1.01, float("nan"), float("inf")])
def test_gate_rejects_invalid_assignment_fraction(fraction: float) -> None:
    with pytest.raises(ValueError):
        assignment_acceleration_decision(fraction)


def test_deferred_gate_retains_exact_cpu_solver_and_full_miss_objective() -> None:
    matched = [[1.0, 3.0], [float("inf"), float("inf")]]
    clutter = [4.0, 5.0]
    misses = [2.0, 5.0]
    decision = assignment_acceleration_decision(0.01)
    assert decision == "defer_cpu"

    actual = assign_partial_cost_matrix(matched, clutter, misses, baseline_constant=0.25)
    expected = optimal_assignments(matched, clutter, misses)
    assert actual.cost == pytest.approx(expected[0].cost + 0.25, abs=1e-8, rel=1e-10)
    assert actual.cost == pytest.approx(
        actual.baseline_constant
        + actual.matched_cost
        + actual.unmatched_report_cost
        + actual.unused_group_cost
    )
    assert actual.unused_groups == (0,)


def test_equal_cost_ties_are_repeatable_and_empty_dimensions_keep_miss_costs() -> None:
    tied = [assign_partial_cost_matrix([[0.0, 0.0]], [10.0], [0.0, 0.0]) for _ in range(4)]
    assert all(item.matches == tied[0].matches for item in tied)
    assert all(item.cost == tied[0].cost == 0.0 for item in tied)

    no_reports = assign_partial_cost_matrix(np.empty((0, 2)), [], [2.0, 3.0], baseline_constant=0.5)
    assert no_reports.matches == ()
    assert no_reports.unused_groups == (0, 1)
    assert no_reports.cost == pytest.approx(5.5)

    no_groups = assign_partial_cost_matrix(np.empty((2, 0)), [2.0, 3.0], [], baseline_constant=0.5)
    assert no_groups.matches == ()
    assert no_groups.unmatched_reports == (0, 1)
    assert no_groups.cost == pytest.approx(5.5)

    empty = assign_likelihood_costs(
        LikelihoodCostMatrix(
            np.empty((0, 0)),
            np.empty(0),
            np.empty(0),
            0.25,
            np.empty((0, 0)),
        )
    )
    assert empty.matches == ()
    assert empty.cost == pytest.approx(0.25)
