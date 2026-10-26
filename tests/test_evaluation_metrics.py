"""Tests for evaluator-only position and association metrics."""

from __future__ import annotations

import pytest

from maritime.evaluation.metrics import evaluate_estimate
from maritime.schemas.estimate import PositionEstimate
from maritime.schemas.truth import EvaluationTruth, TrueObject


def _truth() -> EvaluationTruth:
    return EvaluationTruth(
        observer_east_m=100.0,
        observer_north_m=200.0,
        observer_heading_deg=0.0,
        objects=(
            TrueObject("a", "ship", 500.0, 100.0, "ais-a", 0),
            TrueObject("b", "buoy", 600.0, 300.0, "ais-b", 1),
            TrueObject("c", "ship", 700.0, 400.0, "ais-c", 2),
            # Truth objects not assigned to a reported detection do not count
            # as missed associations for this instantaneous measurement set.
            TrueObject("d", "ship", 800.0, 500.0, "ais-d", None),
            TrueObject("e", "buoy", 900.0, 600.0, None, 3),
        ),
        trial_id="metrics",
    )


def _estimate(
    east_m: float = 100.0,
    north_m: float = 200.0,
    matches: tuple[tuple[int, str], ...] = ((0, "ais-a"), (1, "ais-b"), (2, "ais-c")),
) -> PositionEstimate:
    return PositionEstimate(
        east_m=east_m,
        north_m=north_m,
        score=0.0,
        elapsed_s=0.01,
        algorithm="test",
        matched_pairs=matches,
    )


def test_perfect_estimate_satisfies_default_benchmark_criterion() -> None:
    result = evaluate_estimate(_estimate(), _truth())

    assert result.position_error_m == 0.0
    assert result.correct_associations == 3
    assert result.false_associations == 0
    assert result.missed_associations == 0
    assert result.association_precision == 1.0
    assert result.association_recall == 1.0
    assert result.association_f1 == 1.0
    assert result.success


def test_position_error_is_euclidean_and_threshold_is_inclusive() -> None:
    result = evaluate_estimate(_estimate(east_m=350.0), _truth())

    assert result.position_error_m == 250.0
    assert result.success


def test_out_of_threshold_or_too_few_correct_matches_fails() -> None:
    bad_position = evaluate_estimate(_estimate(east_m=350.01), _truth())
    too_few_matches = evaluate_estimate(
        _estimate(matches=((0, "ais-a"), (1, "wrong"), (8, "extra"))), _truth()
    )

    assert not bad_position.success
    assert too_few_matches.correct_associations == 1
    assert too_few_matches.false_associations == 2
    assert too_few_matches.missed_associations == 2
    assert too_few_matches.association_precision == pytest.approx(1 / 3)
    assert too_few_matches.association_recall == pytest.approx(1 / 3)
    assert too_few_matches.association_f1 == pytest.approx(1 / 3)
    assert not too_few_matches.success


def test_duplicate_matches_count_once_and_unobserved_truth_is_excluded() -> None:
    result = evaluate_estimate(
        _estimate(matches=((0, "ais-a"), (0, "ais-a"), (1, "ais-b"))), _truth()
    )

    assert result.correct_associations == 2
    assert result.false_associations == 0
    assert result.missed_associations == 1
    assert result.association_recall == pytest.approx(2 / 3)


def test_no_matches_have_zero_association_scores_and_fail_default_success() -> None:
    result = evaluate_estimate(_estimate(matches=()), _truth())

    assert result.association_precision == 0.0
    assert result.association_recall == 0.0
    assert result.association_f1 == 0.0
    assert not result.success


@pytest.mark.parametrize("threshold", [-1.0, float("nan"), float("inf")])
def test_invalid_position_threshold_is_rejected(threshold: float) -> None:
    with pytest.raises(ValueError, match="success_position_threshold_m"):
        evaluate_estimate(_estimate(), _truth(), success_position_threshold_m=threshold)


@pytest.mark.parametrize("minimum", [-1, 1.5, True])
def test_invalid_minimum_association_count_is_rejected(minimum: int) -> None:
    with pytest.raises(ValueError, match="success_min_correct_associations"):
        evaluate_estimate(_estimate(), _truth(), success_min_correct_associations=minimum)
