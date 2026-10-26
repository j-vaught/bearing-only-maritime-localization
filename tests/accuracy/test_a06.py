"""A06 exhaustive reference-assignment fixtures and manual checks."""

from __future__ import annotations

import json
from math import inf
from pathlib import Path
from typing import Any

import pytest

from .reference_assignment import (
    Assignment,
    enumerate_partial_assignments,
    optimal_assignments,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "assignment_cases.json"


def load_fixtures() -> list[dict[str, Any]]:
    raw_fixtures = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    for fixture in raw_fixtures:
        fixture["real_costs"] = [
            [inf if cost == "inf" else cost for cost in row] for row in fixture["real_costs"]
        ]
    return raw_fixtures


FIXTURES = load_fixtures()


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda fixture: fixture["name"])
def test_fixture_optimum_and_all_ties_are_retained(fixture: dict[str, Any]) -> None:
    assignments = enumerate_partial_assignments(
        fixture["real_costs"],
        fixture["report_miss_costs"],
        fixture["group_miss_costs"],
    )
    best = optimal_assignments(
        fixture["real_costs"],
        fixture["report_miss_costs"],
        fixture["group_miss_costs"],
    )

    assert assignments
    assert len(best) == fixture["expected_ties"]
    assert best[0].cost == pytest.approx(fixture["expected_cost"], abs=1e-12, rel=0)
    assert best[0].matches == tuple(tuple(pair) for pair in fixture["expected_matches"])
    assert all(assignment.cost >= best[0].cost for assignment in assignments)


def test_manual_unique_assignment_includes_unused_predicted_group_costs() -> None:
    results = enumerate_partial_assignments([[1.0, 9.0], [8.0, 2.0]], [5.0, 5.0], [7.0, 7.0])
    expected = Assignment(
        matches=((0, 0), (1, 1)),
        unmatched_reports=(),
        unused_groups=(),
        cost=3.0,
    )
    assert expected in results
    assert min(result.cost for result in results) == 3.0


def test_forbidden_edge_and_all_unmatched_manual_objectives() -> None:
    results = enumerate_partial_assignments([[0.0, inf]], [3.0], [4.0, 4.0])
    by_matches = {result.matches: result.cost for result in results}
    assert by_matches[((0, 0),)] == 4.0  # edge 0 plus one unused-group miss
    assert by_matches[()] == 11.0  # report miss plus both unused-group misses
    assert all((0, 1) not in result.matches for result in results)


def test_b4_rank_regression_fixture_keeps_later_global_winner() -> None:
    fixture = next(item for item in FIXTURES if item["name"] == "b4_pruning_rank_regression")
    candidates = fixture["candidate_objectives"]
    assert min(candidates, key=candidates.__getitem__) == fixture["expected_candidate_winner"]
    reference = optimal_assignments(
        fixture["real_costs"],
        fixture["report_miss_costs"],
        fixture["group_miss_costs"],
    )
    assert reference[0].cost == candidates[fixture["expected_candidate_winner"]]


@pytest.mark.parametrize(
    ("real_costs", "report_miss_costs", "group_miss_costs"),
    [
        ([[1.0]], [], [1.0]),
        ([[1.0], [2.0]], [1.0, 1.0], [1.0, 1.0]),
        ([[float("nan")]], [1.0], [1.0]),
        ([[-inf]], [1.0], [1.0]),
        ([[1.0]], [float("inf")], [1.0]),
    ],
)
def test_invalid_shapes_and_nonfinite_costs_are_rejected(
    real_costs: list[list[float]],
    report_miss_costs: list[float],
    group_miss_costs: list[float],
) -> None:
    with pytest.raises(ValueError):
        enumerate_partial_assignments(real_costs, report_miss_costs, group_miss_costs)
