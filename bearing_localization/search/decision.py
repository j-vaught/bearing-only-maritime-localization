"""Calibrated acceptance decisions over search outputs, without access to truth."""

from __future__ import annotations

from dataclasses import dataclass
from math import hypot, isfinite
from typing import Literal

from maritime_localization_shared.contracts.localization import LocalizationResult

from .calibration import DecisionCalibration

DecisionStatus = Literal["accepted", "ambiguous", "no_fix"]


@dataclass(frozen=True, slots=True)
class CompetitorCoverageEvidence:
    """Controller evidence that search examined a meaningful spatial region."""

    evaluated_candidate_count: int
    search_extent_m: float
    coverage_complete: bool
    global_bound_excludes_outside: bool = False

    def __post_init__(self) -> None:
        if type(self.evaluated_candidate_count) is not int or self.evaluated_candidate_count < 0:
            raise ValueError("evaluated_candidate_count must be a nonnegative integer.")
        if not isfinite(self.search_extent_m) or self.search_extent_m < 0.0:
            raise ValueError("search_extent_m must be finite and nonnegative.")
        if (
            type(self.coverage_complete) is not bool
            or type(self.global_bound_excludes_outside) is not bool
        ):
            raise TypeError("Coverage flags must be booleans.")


@dataclass(frozen=True, slots=True)
class AccuracyDecision:
    """Decision result with a reason suitable for logs and serialized reports."""

    decision_status: DecisionStatus
    accepted_index: int | None
    reason: str


def decide_localization(
    result: LocalizationResult,
    calibration: DecisionCalibration | None,
    *,
    coverage: CompetitorCoverageEvidence | None = None,
) -> AccuracyDecision:
    """Apply frozen dev calibration to ranked hypotheses and search evidence.

    The call accepts no truth, outcomes, evaluator strata, or trial difficulty.
    A finite-candidate certificate never substitutes for global exclusion.
    """
    if calibration is None:
        return AccuracyDecision("ambiguous", None, "missing_frozen_calibration")
    if not calibration.verified_frozen_artifact or calibration.status != "PASS":
        return AccuracyDecision("ambiguous", None, "calibration_unverified_or_failed")
    if result.search_status == "failed":
        return AccuracyDecision("no_fix", None, "search_failed")
    if result.search_status == "budget_exhausted":
        return AccuracyDecision("ambiguous", None, "search_budget_exhausted")
    if not result.hypotheses:
        return AccuracyDecision("no_fix", None, "no_candidate_hypotheses")

    top = result.hypotheses[0]
    if len(top.assignments) < 3 or len({a.catalog_key for a in top.assignments}) < 3:
        return AccuracyDecision("ambiguous", None, "fewer_than_three_distinct_matched_groups")
    ellipse = top.uncertainty
    if ellipse is None or not isfinite(ellipse.major_radius_m):
        return AccuracyDecision("ambiguous", None, "missing_or_singular_uncertainty_ellipse")
    radius_limit = calibration.max_95_radius_m
    if radius_limit is not None and ellipse.major_radius_m > radius_limit:
        return AccuracyDecision("ambiguous", None, "uncertainty_radius_exceeds_calibrated_limit")
    if coverage is None or not coverage.coverage_complete or coverage.evaluated_candidate_count < 2:
        return AccuracyDecision("ambiguous", None, "incomplete_competitor_coverage")
    if coverage.search_extent_m < 100.0:
        return AccuracyDecision("ambiguous", None, "competitor_search_extent_below_100m")

    distant = next(
        (
            hypothesis
            for hypothesis in result.hypotheses[1:]
            if hypot(hypothesis.east_m - top.east_m, hypothesis.north_m - top.north_m) >= 100.0
        ),
        None,
    )
    if distant is not None and distant.score - top.score < calibration.min_score_gap:
        return AccuracyDecision("ambiguous", None, "distant_competitor_within_calibrated_score_gap")
    valid_global_bound = (
        coverage.global_bound_excludes_outside
        and result.certificate_scope == "continuous_objective"
        and result.bound_gap is not None
        and result.bound_gap <= 1e-8
    )
    if distant is None and not valid_global_bound:
        return AccuracyDecision(
            "ambiguous", None, "no_meaningful_distant_competitor_or_global_exclusion"
        )
    return AccuracyDecision("accepted", 0, "calibrated_unique_fix")


def apply_accuracy_decision(
    result: LocalizationResult,
    calibration: DecisionCalibration | None,
    *,
    coverage: CompetitorCoverageEvidence | None = None,
) -> tuple[LocalizationResult, AccuracyDecision]:
    """Return a shared result updated with the calibrated decision and reason."""
    decision = decide_localization(result, calibration, coverage=coverage)
    updated = LocalizationResult(
        hypotheses=result.hypotheses,
        elapsed_s=result.elapsed_s,
        decision_status=decision.decision_status,
        search_status=result.search_status,
        certificate_scope=result.certificate_scope,
        accepted_index=decision.accepted_index,
        budget_limit=result.budget_limit,
        budget_used=result.budget_used,
        report_count=result.report_count,
        objective_version=result.objective_version,
        bound_gap=result.bound_gap,
    )
    return updated, decision
