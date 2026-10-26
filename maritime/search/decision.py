"""Calibrated acceptance decisions over search outputs, without access to truth."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import hypot, isfinite
from typing import Literal

from maritime.schemas.localization import LocalizationResult

from .calibration import DecisionCalibration
from .modes import SearchModeEvidence, verified_mode_competitor_gap

DecisionStatus = Literal["accepted", "ambiguous", "no_fix"]


@dataclass(frozen=True, slots=True)
class CompetitorCoverageEvidence:
    """Controller evidence about scored candidates and unresolved evaluations."""

    evaluated_candidate_count: int
    search_extent_m: float
    coverage_complete: bool
    global_bound_excludes_outside: bool = False
    unresolved_candidate_count: int | None = 0
    candidate_outcome_counts_available: bool = True
    unresolved_region_count: int = 0
    distant_region_score_lower_bound: float | None = None
    distant_region_bound_scope: str | None = None
    distant_region_bound_top_east_m: float | None = None
    distant_region_bound_top_north_m: float | None = None

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
        if self.unresolved_candidate_count is not None and (
            type(self.unresolved_candidate_count) is not int or self.unresolved_candidate_count < 0
        ):
            raise ValueError("unresolved_candidate_count must be a nonnegative integer or None.")
        if type(self.candidate_outcome_counts_available) is not bool:
            raise TypeError("candidate_outcome_counts_available must be a boolean.")
        if self.candidate_outcome_counts_available != (self.unresolved_candidate_count is not None):
            raise ValueError(
                "unresolved_candidate_count must be present exactly when outcome counts "
                "are available."
            )
        if type(self.unresolved_region_count) is not int or self.unresolved_region_count < 0:
            raise ValueError("unresolved_region_count must be a nonnegative integer.")
        if self.distant_region_score_lower_bound is not None and not isfinite(
            self.distant_region_score_lower_bound
        ):
            raise ValueError("distant_region_score_lower_bound must be finite or None.")
        if self.distant_region_bound_scope not in (
            None,
            "all_objective_positions_at_least_100m_from_top",
        ):
            raise ValueError("Unsupported distant-region bound scope.")
        bound_values = (
            self.distant_region_score_lower_bound,
            self.distant_region_bound_scope,
            self.distant_region_bound_top_east_m,
            self.distant_region_bound_top_north_m,
        )
        if any(value is not None for value in bound_values) and any(
            value is None for value in bound_values
        ):
            raise ValueError(
                "Distant-region bound, scope, and top anchor must be provided together."
            )
        for coordinate in (
            self.distant_region_bound_top_east_m,
            self.distant_region_bound_top_north_m,
        ):
            if coordinate is not None and not isfinite(coordinate):
                raise ValueError("Distant-region bound top anchor must be finite.")


@dataclass(frozen=True, slots=True)
class AccuracyDecision:
    """Decision result with a reason suitable for logs and serialized reports."""

    decision_status: DecisionStatus
    accepted_index: int | None
    reason: str


def _score_tolerance(*scores: float) -> float:
    return max(1e-12, 1e-10 * max((abs(score) for score in scores), default=0.0))


def score_tolerance_for_top(top_score: float) -> float:
    """Return the deterministic comparison tolerance used by runtime decisions."""
    if not isfinite(top_score):
        raise ValueError("top_score must be finite.")
    return _score_tolerance(top_score)


def competitor_gap_clears_threshold(
    score_gap: float, min_score_gap: float, score_tolerance: float
) -> bool:
    """Apply the shared positive competitor-gap predicate."""
    return (
        all(isfinite(value) for value in (score_gap, min_score_gap, score_tolerance))
        and score_gap >= min_score_gap
        and score_gap > score_tolerance
    )


def raw_candidate_vetoes_margin(
    score_gap: float, min_score_gap: float, score_tolerance: float
) -> bool:
    """Return whether a raw distant scored candidate vetoes the calibrated margin."""
    return all(isfinite(value) for value in (score_gap, min_score_gap, score_tolerance)) and (
        score_gap < min_score_gap or score_gap <= score_tolerance
    )


def global_exclusion_evidence_is_valid(
    *,
    search_status: str,
    coverage: CompetitorCoverageEvidence | None,
    certificate_scope: str,
    bound_gap: float | None,
    top_score: float,
    min_score_gap: float,
    score_tolerance: float,
    top_east_m: float,
    top_north_m: float,
) -> bool:
    """Validate the global certificate and its margin against distant basins."""
    scalars = (
        bound_gap,
        top_score,
        min_score_gap,
        score_tolerance,
        top_east_m,
        top_north_m,
    )
    if any(
        not isinstance(value, (int, float)) or isinstance(value, bool) or not isfinite(value)
        for value in scalars
    ):
        return False
    if min_score_gap < 0.0 or score_tolerance < 0.0:
        return False
    if coverage is None or coverage.unresolved_candidate_count not in (None, 0):
        return False
    distant_gap = (
        None
        if coverage.distant_region_score_lower_bound is None
        else coverage.distant_region_score_lower_bound - top_score
    )
    return (
        search_status == "completed"
        and coverage.evaluated_candidate_count >= 2
        and coverage.coverage_complete
        and coverage.global_bound_excludes_outside
        and coverage.unresolved_region_count == 0
        and certificate_scope == "continuous_objective"
        and bound_gap is not None
        and isfinite(bound_gap)
        and bound_gap <= 1e-8
        and coverage.distant_region_bound_scope == "all_objective_positions_at_least_100m_from_top"
        and coverage.distant_region_bound_top_east_m is not None
        and coverage.distant_region_bound_top_north_m is not None
        and hypot(
            coverage.distant_region_bound_top_east_m - top_east_m,
            coverage.distant_region_bound_top_north_m - top_north_m,
        )
        <= 1e-6
        and distant_gap is not None
        and competitor_gap_clears_threshold(distant_gap, min_score_gap, score_tolerance)
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
    uniqueness. A no-mode acceptance requires continuous global exclusion plus
    an explicit margin lower bound over all positions at least 100 m away.
    """
    if calibration is None:
        return AccuracyDecision("ambiguous", None, "missing_frozen_calibration")
    if not calibration.verified_frozen_artifact or calibration.status != "PASS":
        return AccuracyDecision("ambiguous", None, "calibration_unverified_or_failed")
    if not calibration.release_eligible:
        return AccuracyDecision("ambiguous", None, "calibration_not_release_eligible")
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
    if coverage.unresolved_candidate_count is not None and coverage.unresolved_candidate_count:
        return AccuracyDecision("ambiguous", None, "unresolved_evaluated_geometry")

    # A raw far candidate cannot establish a competing mode, but an already
    # scored candidate with a small gap is evidence that the current margin is
    # unsafe. This veto also catches exact ties when a frozen threshold is zero.
    score_tolerance = score_tolerance_for_top(top.score)
    for hypothesis in result.hypotheses[1:]:
        if hypot(hypothesis.east_m - top.east_m, hypothesis.north_m - top.north_m) < 100.0:
            continue
        gap = hypothesis.score - top.score
        if raw_candidate_vetoes_margin(gap, calibration.min_score_gap, score_tolerance):
            return AccuracyDecision(
                "ambiguous", None, "distant_competitor_within_calibrated_score_gap"
            )

    valid_global_bound = global_exclusion_evidence_is_valid(
        search_status=result.search_status,
        coverage=coverage,
        certificate_scope=result.certificate_scope,
        bound_gap=result.bound_gap,
        top_score=top.score,
        min_score_gap=calibration.min_score_gap,
        score_tolerance=score_tolerance,
        top_east_m=top.east_m,
        top_north_m=top.north_m,
    )
    if valid_global_bound:
        return AccuracyDecision("accepted", 0, "calibrated_unique_fix_with_global_exclusion")

    if not coverage.candidate_outcome_counts_available:
        return AccuracyDecision("ambiguous", None, "candidate_outcome_counts_unavailable")

    top_mode_verified, mode_gap = verified_mode_competitor_gap(
        top_east_m=top.east_m,
        top_north_m=top.north_m,
        top_score=top.score,
        modes=tuple(mode_evidence or ()),
    )
    if not top_mode_verified:
        return AccuracyDecision("ambiguous", None, "top_candidate_not_verified_converged_mode")
    if mode_gap is None:
        return AccuracyDecision(
            "ambiguous", None, "no_meaningful_distant_competitor_or_global_exclusion"
        )
    if not competitor_gap_clears_threshold(mode_gap, calibration.min_score_gap, score_tolerance):
        return AccuracyDecision("ambiguous", None, "distant_competitor_within_calibrated_score_gap")
    return AccuracyDecision("accepted", 0, "calibrated_unique_fix_with_verified_modes")


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
