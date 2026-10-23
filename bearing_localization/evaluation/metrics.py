"""Evaluation metrics for position estimates and hidden simulator truth.

This is the only production module that consumes the evaluator-only truth
contract. Search code should never import this module or its truth types.
AIS associations use opaque AIS snapshot record IDs, matching
``PositionEstimate.matched_pairs``.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import hypot, isfinite

from maritime_localization_shared.contracts.estimate import PositionEstimate
from maritime_localization_shared.contracts.truth import EvaluationTruth


@dataclass(frozen=True, slots=True)
class EvaluationMetrics:
    """Position, AIS-association, and benchmark-success metrics for one trial."""

    position_error_m: float
    correct_associations: int
    false_associations: int
    missed_associations: int
    association_precision: float
    association_recall: float
    association_f1: float
    success: bool
    success_position_threshold_m: float
    success_min_correct_associations: int


def evaluate_estimate(
    estimate: PositionEstimate,
    truth: EvaluationTruth,
    *,
    success_position_threshold_m: float = 250.0,
    success_min_correct_associations: int = 3,
) -> EvaluationMetrics:
    """Score an estimate against truth, defaulting to the benchmark criterion.

    A true association is an ``(observation_index, ais_record_id)`` pair for a
    truth object that was assigned to a detected observation. Objects without
    an observation index are invisible to association recall for that trial.
    Duplicate predicted pairs are counted once.
    """
    if not isinstance(estimate, PositionEstimate):
        raise TypeError("estimate must be a PositionEstimate.")
    if not isinstance(truth, EvaluationTruth):
        raise TypeError("truth must be an EvaluationTruth.")
    if not isfinite(success_position_threshold_m) or success_position_threshold_m < 0.0:
        raise ValueError("success_position_threshold_m must be finite and nonnegative.")
    if (
        isinstance(success_min_correct_associations, bool)
        or not isinstance(success_min_correct_associations, int)
        or success_min_correct_associations < 0
    ):
        raise ValueError("success_min_correct_associations must be a nonnegative integer.")

    position_error_m = hypot(
        estimate.east_m - truth.observer_east_m,
        estimate.north_m - truth.observer_north_m,
    )
    true_pairs = {
        (item.observation_index, item.ais_record_id)
        for item in truth.objects
        if item.observation_index is not None and item.ais_record_id is not None
    }
    predicted_pairs = set(estimate.matched_pairs)
    correct = len(true_pairs & predicted_pairs)
    false = len(predicted_pairs - true_pairs)
    missed = len(true_pairs - predicted_pairs)

    precision = correct / len(predicted_pairs) if predicted_pairs else 0.0
    recall = correct / len(true_pairs) if true_pairs else 0.0
    f1 = 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0
    success = (
        position_error_m <= success_position_threshold_m
        and correct >= success_min_correct_associations
    )

    return EvaluationMetrics(
        position_error_m=position_error_m,
        correct_associations=correct,
        false_associations=false,
        missed_associations=missed,
        association_precision=precision,
        association_recall=recall,
        association_f1=f1,
        success=success,
        success_position_threshold_m=success_position_threshold_m,
        success_min_correct_associations=success_min_correct_associations,
    )


__all__ = ["EvaluationMetrics", "evaluate_estimate"]
