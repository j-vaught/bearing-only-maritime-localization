"""Integration checks for the estimator/evaluator truth boundary."""

from __future__ import annotations

from dataclasses import fields
from inspect import signature

from bearing_localization.evaluation.metrics import evaluate_estimate
from bearing_localization.search.grid import run_grid_search
from maritime_localization_shared.contracts.estimate import PositionEstimate
from maritime_localization_shared.contracts.truth import EvaluationTruth, TrueObject


def _estimate(
    matches: tuple[tuple[int, str], ...] = ((0, "ais-a"),),
) -> PositionEstimate:
    return PositionEstimate(
        east_m=100.0,
        north_m=-50.0,
        score=0.0,
        elapsed_s=0.01,
        algorithm="contract-test",
        matched_pairs=matches,
    )


def _truth(
    *,
    heading: float = 0.0,
    unobserved_object: TrueObject | None = None,
) -> EvaluationTruth:
    objects = (
        TrueObject("a", "ship", 1_000.0, 2_000.0, "ais-a", 0),
        TrueObject("b", "buoy", 2_000.0, 1_000.0, "ais-b", 0),
    )
    if unobserved_object is not None:
        objects += (unobserved_object,)
    return EvaluationTruth(
        observer_east_m=100.0,
        observer_north_m=-50.0,
        observer_heading_deg=heading,
        objects=objects,
        trial_id="truth-boundary",
    )


def test_estimator_contract_has_no_truth_input_or_truth_fields() -> None:
    search_parameters = signature(run_grid_search).parameters
    estimate_fields = {field.name for field in fields(PositionEstimate)}

    assert "truth" not in search_parameters
    assert "evaluation_truth" not in search_parameters
    assert "observer_east_m" not in estimate_fields
    assert "observer_north_m" not in estimate_fields
    assert "objects" not in estimate_fields


def test_irrelevant_truth_details_do_not_change_evaluation() -> None:
    estimate = _estimate()
    baseline = evaluate_estimate(estimate, _truth())
    changed_hidden_details = evaluate_estimate(
        estimate,
        _truth(
            heading=143.25,
            unobserved_object=TrueObject(
                "unreported", "shore_structure", -9_000.0, 4_000.0, "ais-hidden", None
            ),
        ),
    )

    assert changed_hidden_details == baseline


def test_merged_group_association_is_scored_per_object_without_false_pair() -> None:
    truth = _truth()

    one_member_matched = evaluate_estimate(_estimate(((0, "ais-a"),)), truth)
    wrong_group_member = evaluate_estimate(_estimate(((0, "ais-c"),)), truth)

    assert one_member_matched.correct_associations == 1
    assert one_member_matched.false_associations == 0
    assert one_member_matched.missed_associations == 1
    assert one_member_matched.association_recall == 0.5
    assert not one_member_matched.success
    assert wrong_group_member.correct_associations == 0
    assert wrong_group_member.false_associations == 1
    assert wrong_group_member.missed_associations == 2


def test_non_ais_truth_object_is_not_an_association_miss() -> None:
    truth = _truth(
        unobserved_object=TrueObject("non-ais", "channel_marker", 3_000.0, 3_000.0, None, 1)
    )

    result = evaluate_estimate(_estimate(((0, "ais-a"),)), truth)

    assert result.correct_associations == 1
    assert result.missed_associations == 1
    assert result.false_associations == 0
