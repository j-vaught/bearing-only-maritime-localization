from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from accuracy.test_a18 import _catalog, _map, _measurements, _xy
from bearing_localization.search.bounds import BoundsResult
from bearing_localization.search.branch_bound import (
    branch_and_bound_search,
)
from bearing_localization.search.config import SearchConfig
from bearing_localization.search.reference_grid import enumerate_grid_points, search_reference_grid
from bearing_localization.search.scoring import CandidateScorer
from maritime_localization_shared.contracts.inputs_v2 import MeasurementSetV2, MeasurementV2
from maritime_localization_shared.contracts.localization import Hypothesis


class _QuadraticScorer:
    def __init__(self, bounds=(0.0, 2.0, 0.0, 1.0), score=None):
        self.config = SearchConfig(bounds_xy=bounds, candidate_budget=100)
        self.measurements = MeasurementSetV2((MeasurementV2(0.0, "2026-10-25T12:00:00Z", 0.5),))
        self.score_fn = score or (lambda x, y: (x - 0.5) ** 2 + (y - 0.5) ** 2 - 3.0)

    def evaluate(self, position_xy):
        x, y = position_xy
        return SimpleNamespace(
            status="scored",
            hypothesis=Hypothesis(x, y, self.score_fn(x, y)),
        )


def _result(bound):
    return BoundsResult(bound, ("same_objective",), None, bound, bound)


def _minimum_square(lo, hi, center):
    return 0.0 if lo <= center <= hi else min((lo - center) ** 2, (hi - center) ** 2)


def _quadratic_bound(context):
    xmin, xmax, ymin, ymax = context.region_xy_m
    score = _minimum_square(xmin, xmax, 0.5) + _minimum_square(ymin, ymax, 0.5) - 3.0
    return _result(score)


def _area(rectangle):
    xmin, xmax, ymin, ymax = rectangle
    return (xmax - xmin) * (ymax - ymin)


def test_exact_region_bounds_close_gap_and_agree_with_small_grid_oracle():
    scorer = _QuadraticScorer()
    result = branch_and_bound_search(
        scorer,
        _quadratic_bound,
        assumptions={"visibility": "fixed", "variance": "fixed"},
    )
    oracle = min(
        scorer.score_fn(x, y)
        for x in (0.0, 0.25, 0.5, 0.75, 1.0)
        for y in (0.0, 0.25, 0.5, 0.75, 1.0)
    )
    assert result.certificate_scope == "continuous_objective"
    assert result.localization.bound_gap == 0.0
    assert result.hypotheses[0].score == pytest.approx(oracle)
    assert result.hypotheses[0].score == -3.0
    assert result.localization.decision_status == "ambiguous"
    assert result.unresolved_regions == ()


def test_longest_dimension_split_is_deterministic_and_tied_optima_survive():
    scorer = _QuadraticScorer(
        score=lambda x, y: min((x - 0.5) ** 2, (x - 1.5) ** 2) + (y - 0.5) ** 2 - 4.0
    )
    visited = []

    def bound(context):
        visited.append((context.region_xy_m, context.depth, context.assumptions))
        xmin, xmax, ymin, ymax = context.region_xy_m
        x_distance = min(
            _minimum_square(xmin, xmax, 0.5),
            _minimum_square(xmin, xmax, 1.5),
        )
        return _result(x_distance + _minimum_square(ymin, ymax, 0.5) - 4.0)

    result = branch_and_bound_search(
        scorer,
        bound,
        assumptions={"grouping": "fixed"},
    )
    assert visited[0][0] == (0.0, 2.0, 0.0, 1.0)
    assert visited[1][0] == (0.0, 1.0, 0.0, 1.0)
    assert visited[2][0] == (1.0, 2.0, 0.0, 1.0)
    assert visited[1][2] == (("grouping", "fixed"),)
    assert result.certificate_scope == "continuous_objective"
    assert [item.score for item in result.hypotheses] == [-4.0, -4.0]
    assert {(item.east_m, item.north_m) for item in result.hypotheses} == {
        (0.5, 0.5),
        (1.5, 0.5),
    }


def test_negative_bounds_and_scores_are_valid():
    scorer = _QuadraticScorer()
    result = branch_and_bound_search(scorer, _quadratic_bound)
    assert result.hypotheses[0].score < 0.0
    assert result.certificate_scope == "continuous_objective"


def test_candidate_budget_retains_full_unresolved_queue_coverage():
    scorer = _QuadraticScorer()
    result = branch_and_bound_search(
        scorer,
        lambda _context: BoundsResult(None, (), "dynamic visibility", None, None),
        max_candidates=1,
    )
    assert result.search_status == "budget_exhausted"
    assert result.localization.decision_status == "ambiguous"
    assert result.certificate_scope == "none"
    assert result.candidates_evaluated == 1
    assert result.unresolved_regions
    assert all(item.lower_bound is None for item in result.unresolved_regions)
    diagnostic = result.diagnostic_dict()
    assert json.loads(json.dumps(diagnostic))["unresolved_regions"]
    assert sum(_area(item.region_xy_m) for item in result.unresolved_regions) == pytest.approx(2.0)


def test_region_budget_and_wall_time_stop_without_false_certificate():
    scorer = _QuadraticScorer()
    by_region = branch_and_bound_search(scorer, _quadratic_bound, max_regions=1)
    assert by_region.search_status == "budget_exhausted"
    assert by_region.certificate_scope == "none"
    assert sum(_area(item.region_xy_m) for item in by_region.unresolved_regions) == pytest.approx(
        2.0
    )


def test_a_callback_without_supported_scope_cannot_issue_a_bound():
    scorer = _QuadraticScorer()
    with pytest.raises(ValueError, match="valid_scope"):
        branch_and_bound_search(
            scorer,
            lambda _context: BoundsResult(0.0, (), None, 0.0, 0.0),
            max_candidates=1,
        )


def test_unresolved_callback_cannot_turn_scored_points_into_a_continuous_certificate():
    scorer = _QuadraticScorer()
    result = branch_and_bound_search(
        scorer,
        lambda _context: BoundsResult(
            None, (), "visibility, grouping, and variance vary with pose", None, None
        ),
        max_candidates=3,
    )
    assert result.candidates_scored == 3
    assert result.certificate_scope == "none"
    assert result.search_status == "budget_exhausted"
    assert result.unresolved_regions


def test_grid_certificate_does_not_transfer_to_the_continuous_objective():
    x, y = _xy()
    bounds = (x - 1.0, x + 1.0, y - 1.0, y + 1.0)
    config = SearchConfig(bounds_xy=bounds, candidate_budget=20, max_range_m=5_000.0)
    measurements, catalog, vector_map = _measurements(), _catalog(), _map()
    grid = search_reference_grid(
        measurements,
        catalog,
        vector_map,
        config,
        spacing_m=1.0,
    )
    assert grid.certificate_scope == "finite_candidate_set"
    assert len(grid.visited_points) == len(tuple(enumerate_grid_points(bounds, 1.0)))

    scorer = CandidateScorer(measurements, catalog, vector_map, config)
    result = branch_and_bound_search(
        scorer,
        lambda _context: BoundsResult(
            None, (), "dynamic visibility and grouping are unsupported", None, None
        ),
        max_candidates=1,
    )
    assert result.candidates_scored == 1
    assert result.certificate_scope == "none"
    assert result.certificate_scope != grid.certificate_scope
    assert result.search_status == "budget_exhausted"
    assert result.localization.decision_status == "ambiguous"
