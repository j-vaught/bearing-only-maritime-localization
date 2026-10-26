"""Release-evaluation gates and support-denominator regressions."""

from __future__ import annotations

import pytest

from bearing_localization.evaluation.metrics import (
    SupportManifest,
    aggregate_release_metrics,
    clustered_bootstrap_interval,
    evaluate_estimate,
    evaluate_localization,
)
from maritime_localization_shared.contracts.estimate import PositionEstimate
from maritime_localization_shared.contracts.localization import (
    Assignment,
    DecisionStatus,
    Hypothesis,
    LocalizationResult,
    SearchStatus,
)
from maritime_localization_shared.contracts.truth import EvaluationTruth, TrueObject


def manifest(*, supported: bool = True, port: str = "p1", placement: str = "x1") -> SupportManifest:
    return SupportManifest(
        valid_reference_water=supported,
        detected_physical_catalog_matched_groups=3 if supported else 2,
        port_id=port,
        placement_id=placement,
    )


def truth() -> EvaluationTruth:
    return EvaluationTruth(
        observer_east_m=0.0,
        observer_north_m=0.0,
        observer_heading_deg=0.0,
        objects=(
            TrueObject("a", "physical_aid", 1_000.0, 1_000.0, "a", 0),
            TrueObject("b", "physical_aid", 1_000.0, -1_000.0, "b", 1),
            TrueObject("c", "physical_aid", 2_000.0, 0.0, "c", 2),
        ),
    )


def result(
    error_m: float = 0.0,
    *,
    decision: DecisionStatus = "accepted",
    search: SearchStatus = "completed",
    assignments: tuple[Assignment, ...] = (
        Assignment(0, "a"),
        Assignment(1, "b"),
        Assignment(2, "c"),
    ),
) -> LocalizationResult:
    hypotheses = (Hypothesis(error_m, 0.0, 0.0, assignments),) if decision == "accepted" else ()
    return LocalizationResult(
        hypotheses=hypotheses,
        elapsed_s=0.25,
        decision_status=decision,
        search_status=search,
        accepted_index=0 if decision == "accepted" else None,
        budget_limit=2 if search == "budget_exhausted" else None,
        budget_used=2 if search == "budget_exhausted" else None,
    )


def test_100m_is_inclusive_and_just_over_fails_accuracy_gate() -> None:
    at_boundary = evaluate_localization(result(100.0), truth(), manifest())
    over_boundary = evaluate_localization(result(100.0001), truth(), manifest())
    summary = aggregate_release_metrics([at_boundary, over_boundary])

    assert at_boundary.within_100m is True
    assert over_boundary.within_100m is False
    assert summary.accepted_count == 2
    assert summary.within_100m_count == 1
    assert summary.conditional_accuracy == 0.5
    assert not summary.release_pass
    assert summary.accepted_error_percentiles_m is not None
    assert dict(summary.accepted_error_percentiles_m)[50] == pytest.approx(100.00005)


def test_ambiguous_budget_crash_timeout_and_missing_reduce_availability() -> None:
    rows = [
        evaluate_localization(result(), truth(), manifest()),
        evaluate_localization(result(decision="ambiguous"), truth(), manifest()),
        evaluate_localization(
            result(decision="no_fix", search="budget_exhausted"), truth(), manifest()
        ),
        evaluate_localization(None, None, manifest(), outcome_status="crash"),
        evaluate_localization(None, None, manifest(), outcome_status="timeout"),
        evaluate_localization(None, None, manifest()),
    ]
    summary = aggregate_release_metrics(rows)

    assert summary.supported_trial_count == 6
    assert summary.accepted_count == 1
    assert summary.availability == pytest.approx(1 / 6)
    assert dict(summary.failure_categories) == {
        "ambiguous": 1,
        "budget_exhausted": 1,
        "crash": 1,
        "missing": 1,
        "timeout": 1,
    }
    assert not summary.release_pass


def test_zero_accepted_is_not_a_release_pass_and_accuracy_is_undefined() -> None:
    rows = [
        evaluate_localization(result(decision="ambiguous"), truth(), manifest()),
        evaluate_localization(None, None, manifest(), outcome_status="crash"),
    ]
    summary = aggregate_release_metrics(rows)

    assert summary.accepted_count == 0
    assert summary.conditional_accuracy is None
    assert summary.conditional_accuracy_wilson_95 is None
    assert not summary.release_pass


def test_unsupported_manifest_does_not_enter_gate_denominator() -> None:
    supported = evaluate_localization(result(), truth(), manifest())
    unsupported = evaluate_localization(None, None, manifest(supported=False))
    summary = aggregate_release_metrics([supported, unsupported])

    assert not unsupported.manifest.supported
    assert summary.trial_count == 2
    assert summary.supported_trial_count == 1
    assert summary.availability == 1.0
    assert summary.release_pass


def test_legacy_estimate_acceptance_is_unknown_and_keeps_explicit_denominator() -> None:
    legacy = PositionEstimate(0.0, 0.0, 0.0, 0.2, "legacy", ((0, "a"),))
    first = evaluate_localization(legacy, truth(), manifest())
    second = evaluate_localization(legacy, truth(), manifest(port="p2", placement="x2"))
    summary = aggregate_release_metrics([first, second])

    assert first.acceptance_status == "legacy_unknown"
    assert first.position_error_m == 0.0
    assert dict(summary.failure_categories) == {"legacy_unknown_acceptance": 2}
    assert summary.supported_trial_count == 2
    assert summary.accepted_count == 0
    assert summary.availability == 0.0
    assert not summary.release_pass
    assert evaluate_estimate(legacy, truth()).success is False


def test_accepted_association_quality_and_failure_categories_are_reported() -> None:
    accepted = evaluate_localization(
        result(assignments=(Assignment(0, "a"), Assignment(1, "wrong"))), truth(), manifest()
    )
    failed = evaluate_localization(
        result(decision="no_fix", search="failed"), truth(), manifest(port="p2")
    )
    summary = aggregate_release_metrics([accepted, failed])

    assert accepted.correct_associations == 1
    assert accepted.false_associations == 1
    assert accepted.missed_associations == 2
    assert summary.association_precision == pytest.approx(0.5)
    assert summary.association_recall == pytest.approx(1 / 3)
    assert summary.association_f1 == pytest.approx(0.4)
    assert dict(summary.failure_categories) == {"failed": 1}


def test_support_manifest_validation_and_clustered_interval_are_deterministic() -> None:
    with pytest.raises(ValueError, match="nonnegative integer"):
        SupportManifest(True, -1)
    rows = [
        evaluate_localization(result(), truth(), manifest(port="p1", placement="x1")),
        evaluate_localization(
            result(decision="ambiguous"), truth(), manifest(port="p1", placement="x1")
        ),
        evaluate_localization(result(), truth(), manifest(port="p2", placement="x2")),
    ]
    first = clustered_bootstrap_interval(rows, resamples=100, seed=7)
    second = clustered_bootstrap_interval(rows, resamples=100, seed=7)

    assert first == second
    assert first is not None
    assert first[0] <= first[1]
