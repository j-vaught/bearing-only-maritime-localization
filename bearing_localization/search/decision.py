"""Calibrated acceptance decisions over search outputs, without access to truth."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import hypot, isfinite
from typing import Literal

from maritime_localization_shared.contracts.localization import LocalizationResult

from .calibration import DecisionCalibration
from .modes import SearchModeEvidence

DecisionStatus = Literal["accepted", "ambiguous", "no_fix"]


@dataclass(frozen=True, slots=True)
class CompetitorCoverageEvidence:
    """Controller evidence about scored candidates and unresolved evaluations."""

    evaluated_candidate_count: int
    search_extent_m: float
    coverage_complete: bool
    global_bound_excludes_outside: bool = False
    unresolved_candidate_count: int = 0

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
        if type(self.unresolved_candidate_count) is not int or self.unresolved_candidate_count < 0:
            raise ValueError("unresolved_candidate_count must be a nonnegative integer.")


@dataclass(frozen=True, slots=True)
class AccuracyDecision:
    """Decision result with a reason suitable for logs and serialized reports."""

    decision_status: DecisionStatus
    accepted_index: int | None
    reason: str


def _score_tolerance(*scores: float) -> float:
    return max(1e-12, 1e-10 * max((abs(score) for score in scores), default=0.0))


def _top_matches_mode(top, mode: SearchModeEvidence) -> bool:
    if hypot(top.east_m - mode.east_m, top.north_m - mode.north_m) > 1e-6:
        return False
    if abs(top.score - mode.score) > _score_tolerance(top.score, mode.score):
        return False
    if tuple(sorted(assignment.catalog_key for assignment in top.assignments)) != tuple(
        sorted(mode.matched_catalog_keys)
    ):
        return False
    if top.uncertainty is None:
        return False
    return abs(top.uncertainty.major_radius_m - mode.radius_95_m) <= max(
        1e-8, 1e-10 * max(top.uncertainty.major_radius_m, mode.radius_95_m)
    )


def decide_localization(
    result: LocalizationResult,
    calibration: DecisionCalibration | None,
    *,
    coverage: CompetitorCoverageEvidence | None = None,
    mode_evidence: Sequence[SearchModeEvidence] | None = None,
) -> AccuracyDecision:
    """Apply frozen dev calibration to ranked hypotheses and search evidence.

    Positive empirical competitor evidence must be independently refined local
    modes. Raw scored hypotheses can veto a small score gap but cannot establish
    uniqueness. A no-competitor acceptance still requires continuous global
    exclusion; finite candidate coverage never substitutes for that proof.
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
    if coverage is None or coverage.evaluated_candidate_count < 2:
        return AccuracyDecision("ambiguous", None, "incomplete_competitor_coverage")
    if coverage.unresolved_candidate_count:
        return AccuracyDecision("ambiguous", None, "unresolved_evaluated_geometry")

    modes = tuple(mode_evidence or ())
    matching_modes = [mode for mode in modes if _top_matches_mode(top, mode)]
    if not matching_modes:
        return AccuracyDecision("ambiguous", None, "top_candidate_not_verified_converged_mode")
    top_mode = min(matching_modes, key=lambda mode: (mode.score, mode.east_m, mode.north_m))
    distant_modes = [
        mode
        for mode in modes
        if hypot(mode.east_m - top_mode.east_m, mode.north_m - top_mode.north_m) >= 100.0
    ]
    verified_competitor = min(
        distant_modes, key=lambda mode: (mode.score, mode.east_m, mode.north_m), default=None
    )

    # A raw far candidate cannot establish a competing mode, but an already
    # scored candidate with a small gap is evidence that the current margin is
    # unsafe. This veto also catches exact ties when a frozen threshold is zero.
    score_tolerance = _score_tolerance(top.score)
    for hypothesis in result.hypotheses[1:]:
        if hypot(hypothesis.east_m - top.east_m, hypothesis.north_m - top.north_m) < 100.0:
            continue
        gap = hypothesis.score - top.score
        if gap < calibration.min_score_gap or gap <= score_tolerance:
            return AccuracyDecision(
                "ambiguous", None, "distant_competitor_within_calibrated_score_gap"
            )

    if verified_competitor is not None:
        mode_gap = verified_competitor.score - top_mode.score
        if mode_gap < calibration.min_score_gap or mode_gap <= score_tolerance:
            return AccuracyDecision(
                "ambiguous", None, "distant_competitor_within_calibrated_score_gap"
            )
        return AccuracyDecision("accepted", 0, "calibrated_unique_fix_with_verified_modes")

    valid_global_bound = (
        coverage.coverage_complete
        and coverage.global_bound_excludes_outside
        and result.certificate_scope == "continuous_objective"
        and result.bound_gap is not None
        and result.bound_gap <= 1e-8
    )
    if not valid_global_bound:
        return AccuracyDecision(
            "ambiguous", None, "no_meaningful_distant_competitor_or_global_exclusion"
        )
    return AccuracyDecision("accepted", 0, "calibrated_unique_fix_with_global_exclusion")


def apply_accuracy_decision(
    result: LocalizationResult,
    calibration: DecisionCalibration | None,
    *,
    coverage: CompetitorCoverageEvidence | None = None,
    mode_evidence: Sequence[SearchModeEvidence] | None = None,
) -> tuple[LocalizationResult, AccuracyDecision]:
    """Return a shared result updated with the calibrated decision and reason."""
    decision = decide_localization(
        result, calibration, coverage=coverage, mode_evidence=mode_evidence
    )
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
