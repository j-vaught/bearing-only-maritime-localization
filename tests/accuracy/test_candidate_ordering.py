from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import numpy as np

from maritime.schemas.localization import Hypothesis
from maritime.search.candidate_order import order_candidate_positions
from maritime.search.config import SearchConfig
from maritime.search.grid import run_grid_search_v2
from maritime.search.hierarchical import run_hierarchical_search_v2
from maritime.search.likelihood import LikelihoodCostMatrix
from maritime.search.prediction import PredictedGroupSet
from maritime.search.scoring import CandidateEvaluation, CandidateScorer


class _Map:
    def __init__(self, statuses):
        self.statuses = statuses

    def point_water_status(self, position):
        return self.statuses(position)


def _evaluation(position, status):
    if status == "unknown":
        status = "unresolved_coverage"
    if status != "water":
        return CandidateEvaluation(status, None, None, None, "fixture")
    east_m, north_m = position
    hypothesis = Hypothesis(east_m=east_m, north_m=north_m, score=east_m + north_m)
    predicted = PredictedGroupSet((), "known", ())
    costs = LikelihoodCostMatrix(
        np.empty((0, 0)),
        np.empty(0),
        np.empty(0),
        0.0,
        np.empty((0, 0)),
    )
    return CandidateEvaluation("scored", hypothesis, predicted, costs)


def _fake_scorer(bounds, budget, statuses):
    config = SearchConfig(
        bounds_xy=bounds,
        candidate_budget=budget,
        algorithm="grid",
        max_range_m=100.0,
    )
    scorer = object.__new__(CandidateScorer)
    scorer.config = config
    scorer.vector_map = cast(Any, _Map(statuses))
    scorer.measurements = cast(Any, SimpleNamespace(measurements=()))
    evaluated = []

    def evaluate(position):
        evaluated.append(position)
        return _evaluation(position, statuses(position))

    scorer.evaluate = cast(Any, evaluate)
    return scorer, evaluated


def test_order_preserves_exact_set_and_prioritizes_known_water():
    positions = [(east, north) for east in range(8) for north in range(8)]

    def statuses(position):
        return "water" if position[0] >= 4 else "unknown"

    ordered = order_candidate_positions(
        reversed(positions),
        water_status=statuses,
        bounds=(0.0, 7.0, 0.0, 7.0),
    )
    assert len(ordered) == len(positions)
    assert set(ordered) == set(positions)
    assert all(statuses(position) == "water" for position in ordered[:32])
    assert len({position[1] for position in ordered[:8]}) >= 4
    assert ordered == order_candidate_positions(
        positions,
        water_status=statuses,
        bounds=(0.0, 7.0, 0.0, 7.0),
    )


def test_grid_budget_uses_known_water_before_unknown_prefix():
    def statuses(position):
        return "water" if position[0] >= 4 else "unknown"

    scorer, evaluated = _fake_scorer((0.0, 7.0, 0.0, 7.0), 4, statuses)
    run = run_grid_search_v2(scorer, grid_spacing_m=1.0)
    assert run.candidate_count == 4
    assert run.localization.search_status == "budget_exhausted"
    assert run.localization.budget_limit == 4
    assert run.localization.budget_used == 4
    assert run.outcome_counts == (
        ("scored", 4),
        ("invalid_water", 0),
        ("unresolved_coverage", 0),
        ("unresolved_model", 0),
    )
    assert all(statuses(position) == "water" for position in evaluated)


def test_hierarchical_reserves_budget_for_refinement_and_reports_exhaustion():
    scorer, evaluated = _fake_scorer((0.0, 12.0, 0.0, 12.0), 8, lambda _position: "water")
    run = run_hierarchical_search_v2(
        scorer,
        coarse_spacing_m=4.0,
        refinement_levels=2,
        refinement_factor=2.0,
        beam_width=2,
    )
    assert run.candidate_count == 8
    assert run.localization.search_status == "budget_exhausted"
    assert run.localization.budget_limit == 8
    assert run.localization.budget_used == 8
    coarse = {(float(east), float(north)) for east in (0, 4, 8, 12) for north in (0, 4, 8, 12)}
    assert any(position not in coarse for position in evaluated)


def test_hierarchical_marks_coarse_quota_truncation_even_without_leaders():
    def statuses(_position):
        return "unknown"

    scorer, evaluated = _fake_scorer((0.0, 12.0, 0.0, 12.0), 4, statuses)
    run = run_hierarchical_search_v2(
        scorer,
        coarse_spacing_m=4.0,
        refinement_levels=2,
        refinement_factor=2.0,
        beam_width=2,
    )
    assert len(evaluated) == run.candidate_count == 4
    assert run.localization.search_status == "budget_exhausted"
    assert run.localization.budget_limit == run.localization.budget_used == 4
    counts = dict(run.outcome_counts)
    assert sum(counts.values()) == run.candidate_count
    assert counts["unresolved_coverage"] == 4


def test_hierarchical_exact_final_stage_quota_can_complete():
    def statuses(_position):
        return "water"

    scorer, evaluated = _fake_scorer((0.0, 1.0, 0.0, 1.0), 4, statuses)
    run = run_hierarchical_search_v2(
        scorer,
        coarse_spacing_m=2.0,
        refinement_levels=0,
    )
    assert len(evaluated) == run.candidate_count == 4
    assert run.localization.search_status == "completed"
    assert run.localization.budget_limit == run.localization.budget_used == 4


def test_explicit_candidate_streams_keep_caller_order():
    positions = [(7.0, 7.0), (0.0, 0.0), (4.0, 4.0), (1.0, 1.0)]

    def statuses(_position):
        return "water"

    grid_scorer, grid_evaluated = _fake_scorer((0.0, 7.0, 0.0, 7.0), len(positions), statuses)
    run_grid_search_v2(grid_scorer, candidate_positions=positions)
    assert grid_evaluated == positions

    hierarchical_scorer, hierarchical_evaluated = _fake_scorer(
        (0.0, 7.0, 0.0, 7.0), len(positions), statuses
    )
    run_hierarchical_search_v2(hierarchical_scorer, candidate_positions=positions)
    assert hierarchical_evaluated == positions


def test_deferred_exact_fit_completes_when_no_planned_candidate_remains():
    def statuses(_position):
        return "unknown"

    scorer, evaluated = _fake_scorer((0.0, 1.0, 0.0, 1.0), 4, statuses)
    run = run_hierarchical_search_v2(
        scorer,
        coarse_spacing_m=2.0,
        refinement_levels=2,
        refinement_factor=2.0,
        beam_width=2,
    )
    assert len(evaluated) == run.candidate_count == 4
    assert run.localization.search_status == "completed"
    assert run.localization.budget_limit == run.localization.budget_used == 4
    counts = dict(run.outcome_counts)
    assert sum(counts.values()) == run.candidate_count == 4


def test_deferred_unique_candidate_beyond_budget_is_exhausted():
    def statuses(_position):
        return "unknown"

    scorer, evaluated = _fake_scorer((0.0, 12.0, 0.0, 12.0), 4, statuses)
    run = run_hierarchical_search_v2(
        scorer,
        coarse_spacing_m=4.0,
        refinement_levels=2,
        refinement_factor=2.0,
        beam_width=2,
    )
    assert len(evaluated) == run.candidate_count == 4
    assert run.localization.search_status == "budget_exhausted"
    assert run.localization.budget_limit == run.localization.budget_used == 4
