from __future__ import annotations

from dataclasses import replace

import pytest
from pyproj import Geod

from bearing_localization.search import assignment_expansion as expansion
from bearing_localization.search.assignment_expansion import (
    ExpansionConfig,
    assignment_expansion,
    expand_assignments,
    propose_assignment_expansion,
)
from bearing_localization.search.config import SearchConfig
from bearing_localization.search.scoring import CandidateEvaluation, CandidateScorer
from maritime_localization_shared.contracts.inputs_v2 import (
    CatalogRecordV2,
    CatalogV2,
    MeasurementSetV2,
    MeasurementV2,
)
from maritime_localization_shared.contracts.localization import Assignment, Hypothesis

from .test_a20 import ORIGIN, _inputs, _xy

_GEOD = Geod(ellps="WGS84")


def _scorer_with_fifth(
    *, bearing: float = 340.0, use_types: bool = True, extra_catalog_records: int = 1
) -> CandidateScorer:
    measurements, catalog, vector_map = _inputs()
    lon, lat, _ = _GEOD.fwd(*ORIGIN, bearing, 2_000.0)
    records = [
        CatalogRecordV2(
            key="target-4",
            longitude_deg=lon,
            latitude_deg=lat,
            normalized_type="vessel",
        )
    ]
    if extra_catalog_records > 1:
        lon_2, lat_2, _ = _GEOD.fwd(*ORIGIN, bearing + 7.0, 2_000.0)
        records.append(
            CatalogRecordV2(
                key="target-5",
                longitude_deg=lon_2,
                latitude_deg=lat_2,
                normalized_type="vessel",
            )
        )
    catalog = CatalogV2(
        catalog.records + tuple(records),
        timestamp_utc=catalog.timestamp_utc,
        source=catalog.source,
        catalog_version=catalog.catalog_version,
        catalog_id="assignment-expansion",
    )
    measurements = MeasurementSetV2(
        measurements.measurements + (MeasurementV2(bearing, "2026-10-25T12:00:00Z", 0.5, "vessel"),)
    )
    x, y = _xy()
    config = SearchConfig(
        bounds_xy=(x - 1_000.0, x + 1_000.0, y - 1_000.0, y + 1_000.0),
        max_range_m=8_000.0,
        use_types=use_types,
    )
    return CandidateScorer(measurements, catalog, vector_map, config)


def _single_addition_seed(scorer: CandidateScorer) -> tuple[Hypothesis, tuple[float, float]]:
    x, y = _xy()
    position = (x + 5.0, y - 5.0)
    exact = scorer.evaluate(position).hypothesis
    assert exact is not None
    assignments = tuple(item for item in exact.assignments if item.report_index < 4)
    seed = replace(exact, score=exact.score + 2.0, assignments=assignments, score_components=None)
    original_evaluate = scorer.evaluate

    def synthetic_evaluate(query: tuple[float, float]) -> CandidateEvaluation:
        if query == position:
            return replace_evaluation(original_evaluate(query), seed)
        return original_evaluate(query)

    setattr(scorer, "evaluate", synthetic_evaluate)
    return seed, position


def replace_evaluation(
    evaluation: CandidateEvaluation, hypothesis: Hypothesis
) -> CandidateEvaluation:

    return CandidateEvaluation(
        "scored", hypothesis, evaluation.predicted, evaluation.costs, evaluation.reason
    )


def test_four_association_basin_can_be_rescued_by_fifth_single_addition():
    scorer = _scorer_with_fifth()
    seed, _ = _single_addition_seed(scorer)
    result = expand_assignments(scorer, seed, config=ExpansionConfig(max_branches=10))

    assert result.status == "completed"
    assert result.completed
    assert result.best_hypothesis.score < seed.score
    assert any(event["status"] == "improved" for event in result.branch_events)
    assert result.counters["eligible_records"] == 1
    assert result.counters["pool_digest"]


def test_unmocked_production_scorer_four_matches_rescued_by_fifth():
    scorer = _scorer_with_fifth(bearing=320.0)
    x, y = _xy()
    seed = scorer.evaluate((x + 20.0, y - 20.0)).hypothesis
    assert seed is not None
    assert tuple(item.report_index for item in seed.assignments) == (1, 2, 3, 4)

    result = expand_assignments(scorer, seed, config=ExpansionConfig())
    assert result.status == "completed"
    assert result.best_hypothesis.score < seed.score
    assert tuple(item.report_index for item in result.best_hypothesis.assignments) == (
        0,
        1,
        2,
        3,
        4,
    )
    assert result.best_hypothesis.assignments[-1].catalog_key == "target-4"
    assert any(event["status"] == "improved" for event in result.branch_events)


def test_clutter_addition_rejected_when_exact_score_worsens():
    scorer = _scorer_with_fifth(bearing=340.0)
    seed, _ = _single_addition_seed(scorer)
    original = scorer.evaluate

    def no_better(query: tuple[float, float]) -> CandidateEvaluation:
        return replace_evaluation(original(query), seed)

    setattr(scorer, "evaluate", no_better)
    result = expand_assignments(scorer, seed, config=ExpansionConfig(max_branches=10))
    assert result.best_hypothesis == seed
    assert result.counters["improved_branches"] == 0
    assert result.branch_events[0]["status"] in {"no_improvement", "converged"}


def test_better_full_score_is_kept_when_candidate_key_is_reassigned():
    scorer = _scorer_with_fifth()
    seed, position = _single_addition_seed(scorer)
    original = scorer.evaluate
    alternate = replace(seed, score=seed.score - 1.0, assignments=(Assignment(0, "other"),))

    def reassigned(query: tuple[float, float]) -> CandidateEvaluation:
        if query == position:
            return replace_evaluation(original(query), seed)
        evaluation = original(query)
        if evaluation.hypothesis is None:
            return evaluation
        return replace_evaluation(evaluation, replace(alternate, east_m=query[0], north_m=query[1]))

    setattr(scorer, "evaluate", reassigned)
    result = expand_assignments(scorer, seed, config=ExpansionConfig(max_branches=10))
    assert result.best_hypothesis.score == alternate.score
    assert result.branch_events[0]["added_assignment_retained"] is False


def test_north_wrap_and_type_blind_aliases_are_deterministic():
    scorer = _scorer_with_fifth(bearing=359.0, use_types=False)
    exact = scorer.evaluate(_xy()).hypothesis
    assert exact is not None
    first = expand_assignments(scorer, exact, config=ExpansionConfig(max_branches=5))
    second = propose_assignment_expansion(scorer, exact, config=ExpansionConfig(max_branches=5))
    third = assignment_expansion(scorer, exact, config=ExpansionConfig(max_branches=5))
    assert first.status == second.status == third.status == "no_unmatched_reports"
    assert first.counters == second.counters == third.counters == {}


def test_covariance_and_common_bias_are_explicitly_rejected():
    scorer = _scorer_with_fifth()
    seed = scorer.evaluate(_xy()).hypothesis
    assert seed is not None
    covariance = MeasurementSetV2(
        scorer.measurements.measurements,
        covariance_deg2=((1.0,) * 5,) * 5,
    )
    covariance_scorer = CandidateScorer(
        covariance, scorer.catalog, scorer.vector_map, scorer.config
    )
    assert (
        expand_assignments(covariance_scorer, seed, config=ExpansionConfig()).status
        == "unsupported_covariance"
    )
    biased = replace(scorer.config, common_bearing_bias_prior_deg=(0.0, 1.0))
    biased_scorer = CandidateScorer(scorer.measurements, scorer.catalog, scorer.vector_map, biased)
    assert (
        expand_assignments(biased_scorer, seed, config=ExpansionConfig()).status
        == "unsupported_calibration"
    )


def test_invalid_and_stale_seed_are_rejected():
    scorer = _scorer_with_fifth()
    seed = scorer.evaluate(_xy()).hypothesis
    assert seed is not None
    stale = replace(seed, score=seed.score + 1.0, score_components=None)
    assert expand_assignments(scorer, stale, config=ExpansionConfig()).status == "stale_hypothesis"
    invalid = replace(seed, east_m=seed.east_m + 5_000.0, score_components=None)
    assert expand_assignments(scorer, invalid, config=ExpansionConfig()).status == "invalid_seed"


def test_invalid_and_insufficient_assignments_have_explicit_statuses():
    scorer = _scorer_with_fifth()
    seed = scorer.evaluate(_xy()).hypothesis
    assert seed is not None
    invalid = replace(seed, assignments=(Assignment(99, "missing"),))
    assert (
        expand_assignments(scorer, invalid, config=ExpansionConfig()).status == "invalid_assignment"
    )
    insufficient = replace(seed, assignments=(seed.assignments[0],))
    assert (
        expand_assignments(scorer, insufficient, config=ExpansionConfig()).status
        == "insufficient_assignments"
    )


def test_configuration_caps_solver_and_branch_budgets():
    with pytest.raises(ValueError, match="max_nfev"):
        ExpansionConfig(max_nfev=101)
    with pytest.raises(ValueError, match="max_branches"):
        ExpansionConfig(max_branches=5_001)


def test_seed_geometry_preparation_is_once_for_multiple_branches(monkeypatch):
    scorer = _scorer_with_fifth(extra_catalog_records=2)
    seed, _ = _single_addition_seed(scorer)
    real_transformer = expansion.Transformer
    calls = 0

    class CountingTransformer:
        @staticmethod
        def from_crs(*args, **kwargs):
            nonlocal calls
            calls += 1
            return real_transformer.from_crs(*args, **kwargs)

    monkeypatch.setattr(expansion, "Transformer", CountingTransformer)
    result = expand_assignments(scorer, seed, config=ExpansionConfig(max_branches=10))
    assert result.counters["candidate_branches"] == 2
    assert calls == 1


def test_budget_callback_and_event_order():
    scorer = _scorer_with_fifth()
    seed, _ = _single_addition_seed(scorer)
    seen: list[tuple[int, int, str]] = []

    def callback(event):
        seen.append((event["branch_id"], event["report_index"], event["catalog_key"]))

    result = expand_assignments(
        scorer,
        seed,
        config=ExpansionConfig(max_branches=1),
        progress_callback=callback,
    )
    assert result.counters["visited_branches"] == 1
    assert result.counters["unvisited_branches"] == 0
    assert len(seen) == 1
    assert result.branch_events[0]["branch_id"] == seen[0][0]


def test_result_callback_receives_each_completed_event_after_append():
    scorer = _scorer_with_fifth()
    seed, _ = _single_addition_seed(scorer)
    seen: list[dict[str, object]] = []

    result = expand_assignments(
        scorer,
        seed,
        config=ExpansionConfig(max_branches=1),
        result_callback=lambda event: seen.append(event),
    )
    assert len(seen) == len(result.branch_events)
    assert seen == list(result.branch_events)


def test_callback_exception_is_not_swallowed():
    scorer = _scorer_with_fifth()
    seed, _ = _single_addition_seed(scorer)

    def callback(_event):
        raise TimeoutError("deadline")

    with pytest.raises(TimeoutError, match="deadline"):
        expand_assignments(
            scorer, seed, config=ExpansionConfig(max_branches=1), progress_callback=callback
        )


def test_negative_score_is_supported_without_truth_inputs():
    scorer = _scorer_with_fifth()
    seed = scorer.evaluate(_xy()).hypothesis
    assert seed is not None
    negative = replace(seed, score=-3.0, score_components=None)
    assert (
        expand_assignments(scorer, negative, config=ExpansionConfig()).status == "stale_hypothesis"
    )
