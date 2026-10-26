from __future__ import annotations

import json
from math import inf
from pathlib import Path

import numpy as np
import pytest

from bearing_localization.search.assignment import (
    assign_likelihood_costs,
    assign_partial_cost_matrix,
)
from bearing_localization.search.likelihood import LikelihoodCostMatrix
from maritime_localization_shared.contracts import AISRecord, Observation

from .reference_assignment import optimal_assignments


def _oracle(costs: LikelihoodCostMatrix):
    best = optimal_assignments(
        costs.matched_costs.tolist(),
        costs.report_clutter_costs.tolist(),
        costs.group_miss_costs.tolist(),
    )
    return min((a.cost for a in best), default=0.0) + costs.baseline_constant


def _load_fixtures():
    path = Path(__file__).parent / "fixtures" / "assignment_cases.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for fixture in rows:
        real = [[inf if x == "inf" else float(x) for x in row] for row in fixture["real_costs"]]
        out.append(
            LikelihoodCostMatrix(
                np.asarray(real, dtype=float).reshape(
                    (len(real), len(fixture["group_miss_costs"]))
                ),
                np.asarray(fixture["report_miss_costs"], dtype=float),
                np.asarray(fixture["group_miss_costs"], dtype=float),
                2.75,
                np.zeros((len(real), len(fixture["group_miss_costs"]))),
            )
        )
    return out


@pytest.mark.parametrize("costs", _load_fixtures())
def test_a06_fixtures_match_exhaustive_full_objective(costs: LikelihoodCostMatrix) -> None:
    result = assign_likelihood_costs(costs)
    assert result.cost == pytest.approx(_oracle(costs), abs=1e-12, rel=0)
    assert len({i for i, _ in result.matches}) == len(result.matches)
    assert len({j for _, j in result.matches}) == len(result.matches)
    assert set(result.unmatched_reports).isdisjoint(i for i, _ in result.matches)
    assert set(result.unused_groups).isdisjoint(j for _, j in result.matches)
    assert result.cost == pytest.approx(
        result.baseline_constant
        + result.matched_cost
        + result.unmatched_report_cost
        + result.unused_group_cost
    )


def test_seeded_random_small_matrices_match_exhaustive_reference() -> None:
    rng = np.random.default_rng(10610)
    for report_count in range(5):
        for group_count in range(5):
            for _ in range(12):
                matched = rng.normal(loc=0.0, scale=5.0, size=(report_count, group_count))
                if matched.size:
                    matched[rng.random(matched.shape) < 0.18] = inf
                report_miss = rng.normal(3.0, 4.0, size=report_count)
                group_miss = rng.normal(2.0, 3.0, size=group_count)
                costs = LikelihoodCostMatrix(
                    matched, report_miss, group_miss, -1.25, np.zeros_like(matched)
                )
                result = assign_likelihood_costs(costs)
                assert result.cost == pytest.approx(_oracle(costs), abs=1e-8, rel=1e-10)


@pytest.mark.parametrize(
    ("matched", "reports", "groups", "expected"),
    [
        (np.empty((0, 0)), [], [], 4.0),
        (np.empty((0, 2)), [], [3.0, -1.0], 6.0),
        (np.empty((2, 0)), [2.0, -3.0], [], 3.0),
        ([[inf]], [1.0], [2.0], 7.0),
    ],
)
def test_empty_axes_forbidden_edges_and_all_unmatched(
    matched, reports, groups, expected: float
) -> None:
    result = assign_partial_cost_matrix(matched, reports, groups, baseline_constant=4.0)
    assert result.matches == ()
    assert result.cost == pytest.approx(expected)


def test_negative_match_costs_and_unused_group_decomposition() -> None:
    result = assign_partial_cost_matrix(
        [[-10.0, 7.0], [8.0, -6.0]],
        [2.0, 3.0],
        [4.0, 5.0],
        baseline_constant=11.0,
    )
    assert result.matches == ((0, 0), (1, 1))
    assert result.unmatched_reports == ()
    assert result.unused_groups == ()
    assert result.matched_cost == -16.0
    assert result.cost == 11.0 - 16.0


def test_zero_delta_ties_and_group_count_change_remain_optimal() -> None:
    tied = assign_partial_cost_matrix([[7.0]], [3.0], [4.0])
    assert tied.cost == pytest.approx(7.0)
    assert (tied.matches == ((0, 0),)) or (
        tied.unmatched_reports == (0,) and tied.unused_groups == (0,)
    )
    for matrix in ([[0.0]], [[0.0, 0.0]], [[0.0], [0.0]], [[0.0, 0.0], [0.0, 0.0]]):
        n_reports = len(matrix)
        n_groups = len(matrix[0])
        result = assign_partial_cost_matrix(matrix, [0.0] * n_reports, [0.0] * n_groups)
        assert result.cost == 0.0


def test_legacy_aliases_unknown_wildcards_and_custom_mapping() -> None:
    from bearing_localization.search.association import compatible_types

    assert compatible_types("ships", "vessel")
    assert compatible_types("buoy", "physical aid")
    assert compatible_types("unknown", "shoreline")
    assert compatible_types("mystery label", "vessel")
    mapping = {"ship": {"class_a"}}
    assert compatible_types("ship", "class_a", sensor_to_ais=mapping)
    assert not compatible_types("ship", "vessel", sensor_to_ais=mapping)


def test_legacy_assignment_api_still_returns_unmatched_and_unused() -> None:
    observation = Observation(0.0, 1.0, "ship")
    ais = (AISRecord("v1", 0.0, 100.0, "vessel"),)
    from bearing_localization.search.association import associate_observations_to_ais

    result = associate_observations_to_ais((0.0, 0.0), (observation,), ais)
    assert result.matches == ((0, 0),)
    assert result.unmatched_observation_indices == ()
    assert result.unused_ais_indices == ()
