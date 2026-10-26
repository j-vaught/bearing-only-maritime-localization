from __future__ import annotations

from dataclasses import replace
from math import hypot

from pyproj import CRS

import maritime.search.assigned_bearing_refinement as assigned_refinement
from maritime.schemas.inputs_v2 import MeasurementSetV2
from maritime.schemas.localization import Assignment, Hypothesis
from maritime.search.assigned_bearing_refinement import refine_assigned_bearing
from maritime.search.config import SearchConfig
from maritime.search.scoring import CandidateEvaluation, CandidateScorer

from .test_continuous_refinement import _inputs, _xy


def _scorer(*, noisy: bool = True, measurements: MeasurementSetV2 | None = None):
    fixture_measurements, catalog, vector_map = _inputs(noisy=noisy)
    if measurements is not None:
        fixture_measurements = measurements
    x, y = _xy()
    config = SearchConfig(
        bounds_xy=(x - 1_000.0, x + 1_000.0, y - 1_000.0, y + 1_000.0),
        max_range_m=8_000.0,
    )
    return CandidateScorer(fixture_measurements, catalog, vector_map, config)


def _seed(scorer: CandidateScorer, *, east_offset: float = 100.0) -> Hypothesis:
    x, y = _xy()
    evaluation = scorer.evaluate((x + east_offset, y - 100.0))
    assert evaluation.hypothesis is not None
    return evaluation.hypothesis


def test_noisy_fixture_improves_with_exact_canonical_hypothesis():
    scorer = _scorer()
    seed = _seed(scorer)
    result = refine_assigned_bearing(scorer, seed)

    assert result.status == "improved"
    assert result.exact_hypothesis is not None
    assert result.exact_hypothesis.score < seed.score
    direct = scorer.evaluate(result.proposed_pose)
    assert direct.hypothesis == result.exact_hypothesis
    assert result.nfev <= 100
    assert result.residual_count == result.assignment_count == len(seed.assignments)
    assert result.canonical_evaluations == 2


def test_unequal_report_sigmas_change_the_fitted_pose():
    scorer = _scorer()
    seed = _seed(scorer, east_offset=50.0)
    mixed = refine_assigned_bearing(scorer, seed)

    measurements = scorer.measurements
    equal = MeasurementSetV2(
        tuple(replace(measurement, sigma_deg=0.5) for measurement in measurements.measurements)
    )
    equal_scorer = _scorer(measurements=equal)
    equal_seed = _seed(equal_scorer, east_offset=50.0)
    equal_result = refine_assigned_bearing(equal_scorer, equal_seed)

    assert mixed.proposed_pose is not None and equal_result.proposed_pose is not None
    assert mixed.proposed_pose != equal_result.proposed_pose


def test_final_canonical_rescore_can_change_assignment_set():
    scorer = _scorer()
    seed = _seed(scorer)
    result = refine_assigned_bearing(scorer, seed)

    assert result.exact_hypothesis is not None
    assert set(a.catalog_key for a in result.exact_hypothesis.assignments) != {
        a.catalog_key for a in seed.assignments
    }


def test_stale_score_keeps_original_exact_hypothesis():
    scorer = _scorer()
    seed = _seed(scorer)
    artificially_best = Hypothesis(
        seed.east_m,
        seed.north_m,
        -100.0,
        assignments=seed.assignments,
        score_components=None,
    )
    result = refine_assigned_bearing(scorer, artificially_best)

    assert result.status == "stale_hypothesis"
    assert result.exact_hypothesis is None
    assert result.original_hypothesis is artificially_best


def test_worse_canonical_proposal_keeps_original_exact_hypothesis(monkeypatch):
    scorer = _scorer()
    seed = _seed(scorer)
    seed_evaluation = scorer.evaluate((seed.east_m, seed.north_m))
    worse = replace(seed, score=seed.score + 1.0, score_components=None)
    outcomes = iter(
        (
            seed_evaluation,
            CandidateEvaluation("scored", worse, seed_evaluation.predicted, seed_evaluation.costs),
        )
    )
    monkeypatch.setattr(CandidateScorer, "evaluate", lambda *_a, **_k: next(outcomes))
    result = refine_assigned_bearing(scorer, seed)

    assert result.status == "no_improvement"
    assert result.exact_hypothesis is None
    assert result.original_hypothesis is seed


def test_invalid_and_insufficient_assignments_are_rejected():
    scorer = _scorer()
    seed = _seed(scorer)
    one = replace(seed, assignments=(seed.assignments[0],))
    insufficient = refine_assigned_bearing(scorer, one)
    assert insufficient.status == "insufficient_assignments"
    assert insufficient.exact_hypothesis is None

    invalid = replace(seed, assignments=(Assignment(0, "missing"), Assignment(1, "other")))
    rejected = refine_assigned_bearing(scorer, invalid)
    assert rejected.status == "invalid_assignment"
    assert rejected.exact_hypothesis is None


def test_common_bias_and_nondiagonal_covariance_are_explicitly_unsupported():
    scorer = _scorer()
    seed = _seed(scorer)
    biased = replace(
        scorer.measurements,
        calibrated_common_bias_mean_deg=0.2,
        calibrated_common_bias_sigma_deg=0.3,
    )
    biased_scorer = _scorer(measurements=biased)
    biased_result = refine_assigned_bearing(biased_scorer, seed)
    assert biased_result.status == "unsupported_calibration"

    covariance = tuple(tuple(0.1 if row == col else 0.01 for col in range(4)) for row in range(4))
    correlated = replace(scorer.measurements, covariance_deg2=covariance)
    correlated_scorer = _scorer(measurements=correlated)
    correlated_result = refine_assigned_bearing(correlated_scorer, seed)
    assert correlated_result.status == "unsupported_covariance"
    assert correlated_result.original_hypothesis is seed


def test_local_bounds_are_clipped_to_global_configuration():
    scorer = _scorer()
    x, y = _xy()
    narrow = SearchConfig(
        bounds_xy=(x + 90.0, x + 110.0, y - 110.0, y - 90.0),
        max_range_m=8_000.0,
    )
    narrow_scorer = CandidateScorer(scorer.measurements, scorer.catalog, scorer.vector_map, narrow)
    narrow_seed = narrow_scorer.evaluate((x + 100.0, y - 100.0)).hypothesis
    assert narrow_seed is not None
    result = refine_assigned_bearing(narrow_scorer, narrow_seed)
    assert result.proposed_pose is not None
    assert narrow.bounds_xy is not None
    assert narrow.bounds_xy[0] <= result.proposed_pose[0] <= narrow.bounds_xy[1]
    assert narrow.bounds_xy[2] <= result.proposed_pose[1] <= narrow.bounds_xy[3]


def test_missing_global_bounds_are_safe():
    scorer = _scorer()
    seed = _seed(scorer)
    no_bounds = replace(scorer.config, bounds_xy=None)
    no_bounds_scorer = CandidateScorer(
        scorer.measurements, scorer.catalog, scorer.vector_map, no_bounds
    )
    no_bounds_result = refine_assigned_bearing(no_bounds_scorer, seed)
    assert no_bounds_result.status == "invalid_bounds"
    assert no_bounds_result.exact_hypothesis is None


def test_stale_hypothesis_is_rejected_before_fitting():
    scorer = _scorer()
    seed = _seed(scorer)
    stale = replace(seed, score=seed.score - 1.0, score_components=None)
    result = refine_assigned_bearing(scorer, stale)

    assert result.status == "stale_hypothesis"
    assert result.exact_hypothesis is None
    assert result.original_hypothesis is stale


def test_solver_max_nfev_returns_original_without_constructor_failure(monkeypatch):
    scorer = _scorer()
    seed = _seed(scorer)

    class MaxedOut:
        x = (seed.east_m, seed.north_m)
        status = 0
        success = False
        nfev = 100
        njev = 100
        message = "maximum evaluations reached"

    monkeypatch.setattr(assigned_refinement, "least_squares", lambda *_a, **_k: MaxedOut())
    result = refine_assigned_bearing(scorer, seed)

    assert result.status == "max_nfev"
    assert result.exact_hypothesis is None
    assert result.nfev == 100


def test_solver_exception_returns_original_without_constructor_failure(monkeypatch):
    scorer = _scorer()
    seed = _seed(scorer)

    def fail(*_args, **_kwargs):
        raise RuntimeError("synthetic solver failure")

    monkeypatch.setattr(assigned_refinement, "least_squares", fail)
    result = refine_assigned_bearing(scorer, seed)

    assert result.status == "solver_failed"
    assert result.exact_hypothesis is None
    assert result.residual_count == len(seed.assignments)


def test_final_invalid_water_and_unknown_coverage_are_rejected(monkeypatch):
    scorer = _scorer()
    seed = _seed(scorer)
    seed_evaluation = scorer.evaluate((seed.east_m, seed.north_m))

    outcomes = iter(
        (
            seed_evaluation,
            CandidateEvaluation("invalid_water", None, None, None, "synthetic land"),
        )
    )
    monkeypatch.setattr(CandidateScorer, "evaluate", lambda *_a, **_k: next(outcomes))
    land = refine_assigned_bearing(scorer, seed)
    assert land.status == "invalid_water"
    assert land.exact_hypothesis is None

    outcomes = iter(
        (
            seed_evaluation,
            CandidateEvaluation("unresolved_coverage", None, None, None, "synthetic unknown"),
        )
    )
    unknown = refine_assigned_bearing(scorer, seed)
    assert unknown.status == "unresolved_coverage"
    assert unknown.exact_hypothesis is None


def test_unsupported_catalog_metadata_is_rejected_before_fitting():
    scorer = _scorer()
    seed = _seed(scorer)
    records = list(scorer.catalog.records)
    records[0] = replace(records[0], metadata=(("antenna_length_m", 4.0),))
    unsupported_catalog = replace(scorer.catalog, records=tuple(records))
    unsupported = CandidateScorer(
        scorer.measurements, unsupported_catalog, scorer.vector_map, scorer.config
    )
    result = refine_assigned_bearing(unsupported, seed)

    assert result.status == "unresolved_model"
    assert result.exact_hypothesis is None


def test_true_north_pose_is_in_projected_utm_coordinates():
    scorer = _scorer()
    seed = _seed(scorer)
    result = refine_assigned_bearing(scorer, seed)
    assert result.proposed_pose is not None
    x, y = _xy()
    assert hypot(result.proposed_pose[0] - x, result.proposed_pose[1] - y) < 1_000.0
    assert scorer.vector_map.crs == CRS.from_epsg(32618)
