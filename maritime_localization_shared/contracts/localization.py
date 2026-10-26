"""Version-two localization result contracts.

The estimator result is independent of simulation truth and preserves all
ranked spatial alternatives, including an explicit decision and search status.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Literal

DecisionStatus = Literal["accepted", "ambiguous", "no_fix"]
SearchStatus = Literal["completed", "budget_exhausted", "failed"]
CertificateScope = Literal["none", "finite_candidate_set", "continuous_objective"]


@dataclass(frozen=True, slots=True)
class Assignment:
    """One injective report-to-catalog association for a hypothesis."""

    report_index: int
    catalog_key: str
    residual_deg: float | None = None
    cost: float | None = None

    def __post_init__(self) -> None:
        if type(self.report_index) is not int or self.report_index < 0:
            raise ValueError("report_index must be a nonnegative integer.")
        if not isinstance(self.catalog_key, str) or not self.catalog_key.strip():
            raise ValueError("catalog_key must be a nonempty string.")
        for name, value in (("residual_deg", self.residual_deg), ("cost", self.cost)):
            if value is not None and not isfinite(value):
                raise ValueError(f"{name} must be finite or None.")


@dataclass(frozen=True, slots=True)
class UncertaintyEllipse:
    """A confidence ellipse in projected coordinates, with radii measured in meters."""

    major_radius_m: float
    minor_radius_m: float
    orientation_deg: float
    confidence_level: float = 0.95

    def __post_init__(self) -> None:
        if not all(
            isfinite(value)
            for value in (
                self.major_radius_m,
                self.minor_radius_m,
                self.orientation_deg,
                self.confidence_level,
            )
        ):
            raise ValueError("Uncertainty ellipse values must be finite.")
        if self.major_radius_m < self.minor_radius_m or self.minor_radius_m < 0.0:
            raise ValueError("Ellipse radii must satisfy major_radius_m >= minor_radius_m >= 0.")
        if not 0.0 < self.confidence_level < 1.0:
            raise ValueError("confidence_level must be strictly between zero and one.")


@dataclass(frozen=True, slots=True)
class ScoreComponents:
    """Additive negative-log-score decomposition for a candidate."""

    matched_cost: float
    clutter_cost: float
    missed_cost: float
    total_cost: float

    def __post_init__(self) -> None:
        values = (self.matched_cost, self.clutter_cost, self.missed_cost, self.total_cost)
        if not all(isfinite(value) for value in values):
            raise ValueError("Score components must all be finite.")
        parts = self.matched_cost + self.clutter_cost + self.missed_cost
        if not isclose_sum(self.total_cost, parts):
            raise ValueError("total_cost must equal the sum of the score components.")


def isclose_sum(total: float, parts: float) -> bool:
    """Use the contract's documented exact-objective comparison tolerance."""
    return abs(total - parts) <= max(1e-8, 1e-10 * max(abs(total), abs(parts)))


@dataclass(frozen=True, slots=True)
class Hypothesis:
    """A ranked spatial candidate with explicit uncertainty and associations."""

    east_m: float
    north_m: float
    score: float
    assignments: tuple[Assignment, ...] = ()
    uncertainty: UncertaintyEllipse | None = None
    score_components: ScoreComponents | None = None

    def __post_init__(self) -> None:
        if not all(isfinite(value) for value in (self.east_m, self.north_m, self.score)):
            raise ValueError("Hypothesis coordinates and score must be finite.")
        if not isinstance(self.assignments, tuple) or any(
            not isinstance(item, Assignment) for item in self.assignments
        ):
            raise TypeError("assignments must be a tuple of Assignment records.")
        report_indices = [item.report_index for item in self.assignments]
        catalog_keys = [item.catalog_key for item in self.assignments]
        if len(report_indices) != len(set(report_indices)):
            raise ValueError("A hypothesis may assign each report at most once.")
        if len(catalog_keys) != len(set(catalog_keys)):
            raise ValueError("A hypothesis may assign each catalog key at most once.")
        if self.uncertainty is not None and not isinstance(self.uncertainty, UncertaintyEllipse):
            raise TypeError("uncertainty must be an UncertaintyEllipse or None.")
        if self.score_components is not None:
            if not isinstance(self.score_components, ScoreComponents):
                raise TypeError("score_components must be ScoreComponents or None.")
            if not isclose_sum(self.score, self.score_components.total_cost):
                raise ValueError("score must equal score_components.total_cost.")


@dataclass(frozen=True, slots=True)
class LocalizationResult:
    """Ranked hypotheses and the explicit decision made by a search run."""

    hypotheses: tuple[Hypothesis, ...]
    elapsed_s: float
    decision_status: DecisionStatus
    search_status: SearchStatus
    certificate_scope: CertificateScope = "none"
    accepted_index: int | None = None
    budget_limit: int | None = None
    budget_used: int | None = None
    report_count: int | None = None
    objective_version: str = "bearing_set_map_v1"
    bound_gap: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.hypotheses, tuple) or any(
            not isinstance(item, Hypothesis) for item in self.hypotheses
        ):
            raise TypeError("hypotheses must be a tuple of Hypothesis records.")
        if not isclose_scores(self.hypotheses):
            raise ValueError("hypotheses must be ranked in nondecreasing score order.")
        if not isfinite(self.elapsed_s) or self.elapsed_s < 0.0:
            raise ValueError("elapsed_s must be finite and nonnegative.")
        if self.decision_status not in ("accepted", "ambiguous", "no_fix"):
            raise ValueError("decision_status must be accepted, ambiguous, or no_fix.")
        if self.search_status not in ("completed", "budget_exhausted", "failed"):
            raise ValueError("search_status must be completed, budget_exhausted, or failed.")
        if self.certificate_scope not in ("none", "finite_candidate_set", "continuous_objective"):
            raise ValueError("Unsupported certificate_scope.")
        if not isinstance(self.objective_version, str) or not self.objective_version.strip():
            raise ValueError("objective_version must be a nonempty version identifier.")
        if self.bound_gap is not None and (not isfinite(self.bound_gap) or self.bound_gap < 0.0):
            raise ValueError("bound_gap must be finite and nonnegative or None.")
        if self.certificate_scope == "none" and self.bound_gap is not None:
            raise ValueError("bound_gap requires a non-none certificate_scope.")
        if self.accepted_index is not None and (
            type(self.accepted_index) is not int
            or not 0 <= self.accepted_index < len(self.hypotheses)
        ):
            raise ValueError("accepted_index must select a ranked hypothesis.")
        if self.decision_status == "accepted":
            if self.accepted_index is None:
                raise ValueError("An accepted result must select a hypothesis.")
            if self.search_status != "completed":
                raise ValueError("A result cannot be accepted unless the search completed.")
            if self.budget_limit is not None and self.budget_used == self.budget_limit:
                raise ValueError("A result cannot be accepted after exhausting its budget.")
        elif self.accepted_index is not None:
            raise ValueError("Only an accepted result may set accepted_index.")
        if self.report_count is not None and (
            type(self.report_count) is not int or self.report_count < 0
        ):
            raise ValueError("report_count must be a nonnegative integer or None.")
        if self.report_count is not None and any(
            assignment.report_index >= self.report_count
            for hypothesis in self.hypotheses
            for assignment in hypothesis.assignments
        ):
            raise ValueError("Assignment report_index must be below report_count.")
        if (self.budget_limit is None) != (self.budget_used is None):
            raise ValueError("budget_limit and budget_used must be provided together.")
        if self.budget_limit is not None:
            if type(self.budget_limit) is not int or self.budget_limit < 1:
                raise ValueError("budget_limit must be a positive integer.")
            if type(self.budget_used) is not int or not 0 <= self.budget_used <= self.budget_limit:
                raise ValueError("budget_used must be an integer within budget_limit.")
        if self.search_status == "budget_exhausted":
            if self.budget_limit is None or self.budget_used != self.budget_limit:
                raise ValueError("budget_exhausted requires a fully consumed declared budget.")
        if self.search_status == "failed" and self.decision_status == "accepted":
            raise ValueError("A failed search cannot produce an accepted result.")


def isclose_scores(hypotheses: tuple[Hypothesis, ...]) -> bool:
    """Return whether ranked hypothesis scores are in nondecreasing order."""
    return all(left.score <= right.score for left, right in zip(hypotheses, hypotheses[1:]))
