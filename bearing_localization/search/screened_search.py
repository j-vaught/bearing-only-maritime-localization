"""Exact finite-candidate scoring with certified visibility-based screening.

Every candidate is either pruned by a supported objective lower bound backed by
a certified visible-group count, or passed unchanged to CandidateScorer.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from math import isfinite
from time import perf_counter
from typing import Literal

from maritime_localization_shared.contracts.localization import Hypothesis, LocalizationResult

from .candidate_lower_bound import (
    CandidateLowerBoundResult,
    PreparedCandidateObjectiveBound,
    prepare_candidate_objective_bound,
    should_prune_candidate,
)
from .scoring import CandidateEvaluation, CandidateScorer, CandidateStatus
from .visibility_screen import VisibilityScreenResult, screen_candidate_visibility

CandidateAction = Literal["exact_scored", "exact_invalid_water", "exact_unresolved", "pruned"]


@dataclass(frozen=True, slots=True)
class CandidatePosition:
    """One deterministic candidate ID and its projected observer position."""

    candidate_id: str
    position_xy_m: tuple[float, float]

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_id, str) or not self.candidate_id.strip():
            raise ValueError("candidate_id must be a nonempty string.")
        if len(self.position_xy_m) != 2 or not all(
            isfinite(float(value)) for value in self.position_xy_m
        ):
            raise ValueError("position_xy_m must contain two finite coordinates.")


@dataclass(frozen=True, slots=True)
class FeasibleIncumbent:
    """A feasible upper bound produced by an exact call to the same scorer."""

    candidate_id: str
    position_xy_m: tuple[float, float]
    evaluation: CandidateEvaluation
    _scorer_token: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_id, str) or not self.candidate_id.strip():
            raise ValueError("candidate_id must be a nonempty string.")
        if not isinstance(self.evaluation, CandidateEvaluation):
            raise TypeError("evaluation must be an exact CandidateEvaluation.")
        if self.evaluation.status != "scored" or self.evaluation.hypothesis is None:
            raise ValueError("A feasible incumbent requires a fully scored candidate.")
        hypothesis = self.evaluation.hypothesis
        if (hypothesis.east_m, hypothesis.north_m) != tuple(map(float, self.position_xy_m)):
            raise ValueError("Incumbent position must match its scored hypothesis.")

    @property
    def hypothesis(self) -> Hypothesis:
        assert self.evaluation.hypothesis is not None
        return self.evaluation.hypothesis

    @property
    def score(self) -> float:
        return self.hypothesis.score


@dataclass(frozen=True, slots=True)
class CandidateVisit:
    """Auditable disposition for one visited candidate."""

    candidate_id: str
    position_xy_m: tuple[float, float]
    action: CandidateAction
    visibility: VisibilityScreenResult | None
    lower_bound: CandidateLowerBoundResult | None
    evaluation_status: CandidateStatus | None
    score: float | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class ScreenedSearchResult:
    """Finite candidate result, exact-score alternatives, and run accounting."""

    localization: LocalizationResult
    visits: tuple[CandidateVisit, ...]
    candidate_count: int
    exact_scored_count: int
    exact_evaluation_count: int
    invalid_water_count: int
    unresolved_count: int
    pruned_count: int
    unvisited_count: int | None
    unvisited_candidate_ids: tuple[str, ...]
    first_unvisited_candidate_id: str | None
    best_scored_candidate: FeasibleIncumbent | None
    total_elapsed_s: float
    visibility_elapsed_s: float
    bound_elapsed_s: float
    exact_score_elapsed_s: float

    @property
    def hypotheses(self) -> tuple[Hypothesis, ...]:
        return self.localization.hypotheses

    @property
    def search_status(self) -> str:
        return self.localization.search_status

    @property
    def certificate_scope(self) -> str:
        return self.localization.certificate_scope


def screened_candidate_search(
    scorer: CandidateScorer,
    candidates: Iterable[CandidatePosition],
    *,
    visit_budget: int,
    complete_candidate_count: int | None = None,
    feasible_incumbent: FeasibleIncumbent | None = None,
    comparison_margin: float | None = None,
) -> ScreenedSearchResult:
    """Search a deterministic finite candidate stream with exact fallback.

    The prepared objective bound is computed once from the scorer's immutable
    measurements/configuration. For each water candidate, visibility screening
    stops at the smallest useful certified group count K, if one exists. An
    unsupported screen or bound falls back to an unchanged exact scorer call.
    """
    if not isinstance(scorer, CandidateScorer):
        raise TypeError("scorer must be a CandidateScorer.")
    if type(visit_budget) is not int or visit_budget < 1:
        raise ValueError("visit_budget must be a positive integer.")
    if complete_candidate_count is not None and (
        type(complete_candidate_count) is not int or complete_candidate_count < 0
    ):
        raise ValueError("complete_candidate_count must be a nonnegative integer or None.")
    if feasible_incumbent is not None:
        if not isinstance(feasible_incumbent, FeasibleIncumbent):
            raise TypeError("feasible_incumbent must be a FeasibleIncumbent from exact scoring.")
        if feasible_incumbent._scorer_token is not scorer:
            raise ValueError("feasible_incumbent must come from this CandidateScorer instance.")
    if isinstance(candidates, Sequence):
        if complete_candidate_count is None:
            complete_candidate_count = len(candidates)
        elif len(candidates) != complete_candidate_count:
            raise ValueError("candidate sequence length does not match complete_candidate_count.")
    if comparison_margin is None:
        requested_margin = 0.0
    else:
        requested_margin = float(comparison_margin)
        if not isfinite(requested_margin) or requested_margin < 0.0:
            raise ValueError("comparison_margin must be finite and nonnegative.")

    prediction_context = scorer._prediction_context
    max_group_count = len(prediction_context.records)
    iterator = iter(candidates)
    sequence_candidates = candidates if isinstance(candidates, Sequence) else None
    visits: list[CandidateVisit] = []
    hypotheses: list[Hypothesis] = []
    counts: Counter[CandidateStatus] = Counter()
    seen_ids: set[str] = set()
    best = feasible_incumbent
    if best is not None:
        hypotheses.append(best.hypothesis)

    visibility_elapsed = score_elapsed = 0.0
    preparation_start = perf_counter()
    run_started = perf_counter()
    prepared_bound = prepare_candidate_objective_bound(
        scorer.measurements,
        scorer.config,
        variance_model_supported=_variance_model_supported(scorer),
    )
    bound_elapsed = perf_counter() - preparation_start
    bound_supported = prepared_bound.supported
    exhausted = False
    first_unvisited_id: str | None = None
    for _ in range(visit_budget):
        try:
            point = next(iterator)
        except StopIteration:
            exhausted = True
            break
        if not isinstance(point, CandidatePosition):
            raise TypeError("candidates must yield CandidatePosition instances.")
        if point.candidate_id in seen_ids:
            raise ValueError(f"Duplicate candidate_id {point.candidate_id!r}.")
        seen_ids.add(point.candidate_id)
        position = (float(point.position_xy_m[0]), float(point.position_xy_m[1]))
        evaluation: CandidateEvaluation | None = None
        visibility: VisibilityScreenResult | None = None
        lower: CandidateLowerBoundResult | None = None

        if best is not None and bound_supported:
            candidate_margin = max(
                requested_margin,
                scorer.config.absolute_score_tolerance
                + scorer.config.relative_score_tolerance * abs(best.score),
            )
            threshold = _minimum_useful_group_threshold(
                prepared_bound,
                max_group_count,
                best.score,
                candidate_margin,
            )
            screen_start = perf_counter()
            visibility = screen_candidate_visibility(
                position,
                prediction_context,
                stop_after_minimum_groups=threshold,
                scan_all_members=threshold is None,
            )
            visibility_elapsed += perf_counter() - screen_start
            if visibility.observer_status == "land":
                threshold = None
            if visibility.certified and visibility.observer_status != "land":
                bound_start = perf_counter()
                lower = prepared_bound.bound(
                    visibility.minimum_visible_group_count,
                    True,
                )
                bound_elapsed += perf_counter() - bound_start
                if should_prune_candidate(lower, best.score, comparison_margin=candidate_margin):
                    visits.append(
                        CandidateVisit(
                            point.candidate_id,
                            position,
                            "pruned",
                            visibility,
                            lower,
                            None,
                            None,
                            "supported objective lower bound exceeds exact feasible incumbent",
                        )
                    )
                    continue

        score_start = perf_counter()
        evaluation = scorer.evaluate(position)
        score_elapsed += perf_counter() - score_start
        counts[evaluation.status] += 1
        if evaluation.status == "scored" and evaluation.hypothesis is not None:
            hypothesis = evaluation.hypothesis
            hypotheses.append(hypothesis)
            candidate_incumbent = FeasibleIncumbent(
                point.candidate_id,
                position,
                evaluation,
                scorer,
            )
            if best is None or hypothesis.score < best.score:
                best = candidate_incumbent
            action: CandidateAction = "exact_scored"
        elif evaluation.status == "invalid_water":
            action = "exact_invalid_water"
        else:
            action = "exact_unresolved"
        visits.append(
            CandidateVisit(
                point.candidate_id,
                position,
                action,
                visibility,
                lower,
                evaluation.status,
                None if evaluation.hypothesis is None else evaluation.hypothesis.score,
                evaluation.reason,
            )
        )

    if not exhausted:
        if complete_candidate_count is not None and len(visits) < complete_candidate_count:
            unvisited_count = complete_candidate_count - len(visits)
            if sequence_candidates is not None:
                unvisited_candidate_ids = tuple(
                    item.candidate_id for item in sequence_candidates[len(visits) :]
                )
                first_unvisited_id = unvisited_candidate_ids[0] if unvisited_candidate_ids else None
            else:
                unvisited_candidate_ids = ()
                try:
                    first_unvisited_id = next(iterator).candidate_id
                except StopIteration:
                    unvisited_count = 0
                    exhausted = True
        else:
            unvisited_count = 0
            unvisited_candidate_ids = ()
            try:
                next_point = next(iterator)
            except StopIteration:
                exhausted = True
            else:
                first_unvisited_id = next_point.candidate_id
                if complete_candidate_count is None:
                    unvisited_count = None
                else:
                    unvisited_count = max(0, complete_candidate_count - len(visits))
                    if unvisited_count == 0:
                        raise ValueError("candidate stream exceeded complete_candidate_count.")
                unvisited_candidate_ids = (
                    tuple(item.candidate_id for item in sequence_candidates[len(visits) :])
                    if sequence_candidates is not None
                    else (first_unvisited_id,)
                )
    else:
        unvisited_count = 0
        unvisited_candidate_ids = ()

    if (
        complete_candidate_count is not None
        and exhausted
        and len(visits) != complete_candidate_count
    ):
        raise ValueError("candidate stream length does not match complete_candidate_count.")
    if (
        complete_candidate_count is not None
        and not exhausted
        and len(visits) > complete_candidate_count
    ):
        raise ValueError("candidate stream exceeded complete_candidate_count.")

    ranked = tuple(sorted(hypotheses, key=lambda item: (item.score, item.east_m, item.north_m)))
    status: Literal["completed", "budget_exhausted"] = (
        "completed" if exhausted else "budget_exhausted"
    )
    supported_finite_set = (
        exhausted
        and len(visits) > 0
        and counts["unresolved_coverage"] == 0
        and counts["unresolved_model"] == 0
    )
    total_elapsed = perf_counter() - run_started
    localization = LocalizationResult(
        hypotheses=ranked,
        elapsed_s=total_elapsed,
        decision_status="ambiguous" if ranked else "no_fix",
        search_status=status,
        certificate_scope="finite_candidate_set" if supported_finite_set else "none",
        budget_limit=visit_budget,
        budget_used=len(visits),
        report_count=len(scorer.measurements.measurements),
        objective_version=scorer.config.objective_version,
    )
    return ScreenedSearchResult(
        localization=localization,
        visits=tuple(visits),
        candidate_count=len(visits),
        exact_scored_count=counts["scored"],
        exact_evaluation_count=sum(counts.values()),
        invalid_water_count=counts["invalid_water"],
        unresolved_count=counts["unresolved_coverage"] + counts["unresolved_model"],
        pruned_count=sum(visit.action == "pruned" for visit in visits),
        unvisited_count=unvisited_count,
        unvisited_candidate_ids=unvisited_candidate_ids,
        first_unvisited_candidate_id=first_unvisited_id,
        best_scored_candidate=best,
        total_elapsed_s=total_elapsed,
        visibility_elapsed_s=visibility_elapsed,
        bound_elapsed_s=bound_elapsed,
        exact_score_elapsed_s=score_elapsed,
    )


def _variance_model_supported(scorer: CandidateScorer) -> bool:
    """Enable the floor only for the diagonal finite-sigma model it covers."""
    measurements = scorer.measurements
    if (
        measurements.calibrated_common_bias_mean_deg is not None
        or measurements.calibrated_common_bias_sigma_deg is not None
    ):
        return False
    covariance = measurements.covariance_deg2
    if covariance is None:
        return True
    return all(
        covariance[row][column] == 0.0
        for row in range(len(covariance))
        for column in range(len(covariance))
        if row != column
    )


def _minimum_useful_group_threshold(
    prepared: PreparedCandidateObjectiveBound,
    maximum_group_count: int,
    feasible_score: float,
    margin: float,
) -> int | None:
    """Find the smallest useful K using cached prepared likelihood floors."""
    if maximum_group_count <= 0 or not prepared.supported:
        return None
    low, high = 0, 1
    while high <= maximum_group_count:
        result = prepared.bound(high, True)
        if should_prune_candidate(result, feasible_score, comparison_margin=margin):
            break
        low, high = high, high * 2
    if high > maximum_group_count:
        high = maximum_group_count
        result = prepared.bound(high, True)
        if not should_prune_candidate(result, feasible_score, comparison_margin=margin):
            return None
    while low + 1 < high:
        middle = (low + high) // 2
        result = prepared.bound(middle, True)
        if should_prune_candidate(result, feasible_score, comparison_margin=margin):
            high = middle
        else:
            low = middle
    return high


__all__ = [
    "CandidatePosition",
    "CandidateVisit",
    "FeasibleIncumbent",
    "ScreenedSearchResult",
    "screened_candidate_search",
]
