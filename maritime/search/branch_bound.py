"""Deterministic branch-and-bound over a declared rectangular search domain.

The controller evaluates every feasible incumbent through CandidateScorer and
delegates region lower bounds to an explicit callback. A callback must return a
sound bound for the supplied domain and assumptions; unsupported regimes stay
in the queue as unresolved diagnostics.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from math import isfinite
from time import perf_counter
from typing import Literal, Protocol

from maritime.schemas.inputs_v2 import MeasurementSetV2
from maritime.schemas.localization import Hypothesis, LocalizationResult

from .bounds import BoundsResult
from .config import SearchConfig
from .scoring import CandidateScorer, CandidateStatus

Rectangle = tuple[float, float, float, float]


class Evaluation(Protocol):
    @property
    def status(self) -> CandidateStatus: ...

    @property
    def hypothesis(self) -> Hypothesis | None: ...


class Scorer(Protocol):
    """Structural interface implemented by CandidateScorer."""

    config: SearchConfig
    measurements: MeasurementSetV2

    def evaluate(self, position_xy: tuple[float, float]) -> Evaluation: ...


BoundCallback = Callable[["BranchBoundContext"], BoundsResult]


@dataclass(frozen=True, slots=True)
class BranchBoundContext:
    """Inputs a region-bound callback needs to justify its lower bound."""

    scorer: Scorer
    region_xy_m: Rectangle
    declared_bounds_xy_m: Rectangle
    depth: int
    assumptions: tuple[tuple[str, object], ...]


@dataclass(frozen=True, slots=True)
class RegionDiagnostic:
    """A queued region retained because it has not been safely excluded."""

    region_xy_m: Rectangle
    depth: int
    lower_bound: float | None
    valid_scope: tuple[str, ...]
    reason: str | None


@dataclass(frozen=True, slots=True)
class BranchBoundResult:
    """Search output plus the complete unresolved queue at termination."""

    localization: LocalizationResult
    regions_created: int
    regions_processed: int
    candidates_evaluated: int
    candidates_scored: int
    regions_pruned: int
    unresolved_regions: tuple[RegionDiagnostic, ...]
    stop_reason: str

    @property
    def hypotheses(self) -> tuple[Hypothesis, ...]:
        return self.localization.hypotheses

    @property
    def search_status(self) -> str:
        return self.localization.search_status

    @property
    def certificate_scope(self) -> str:
        return self.localization.certificate_scope

    def diagnostic_dict(self) -> dict[str, object]:
        """Return JSON-compatible search and open-queue diagnostics."""
        return {
            "search_status": self.search_status,
            "certificate_scope": self.certificate_scope,
            "stop_reason": self.stop_reason,
            "regions_created": self.regions_created,
            "regions_processed": self.regions_processed,
            "candidates_evaluated": self.candidates_evaluated,
            "candidates_scored": self.candidates_scored,
            "regions_pruned": self.regions_pruned,
            "unresolved_regions": [
                {
                    "region_xy_m": list(item.region_xy_m),
                    "depth": item.depth,
                    "lower_bound": item.lower_bound,
                    "valid_scope": list(item.valid_scope),
                    "reason": item.reason,
                }
                for item in self.unresolved_regions
            ],
        }


@dataclass(frozen=True, slots=True)
class _Region:
    bounds: Rectangle
    depth: int
    lower_bound: float | None
    valid_scope: tuple[str, ...]
    reason: str | None
    sampled: bool = False


def branch_and_bound_search(
    scorer: Scorer,
    bound_callback: BoundCallback,
    *,
    assumptions: Mapping[str, object] | None = None,
    max_candidates: int | None = None,
    max_regions: int | None = None,
    max_depth: int = 64,
) -> BranchBoundResult:
    """Search the complete finite config.bounds_xy domain with region bounds.

    The callback receives the current region, declared domain, scorer inputs
    and configuration, caller assumptions, and split depth. It must return a
    valid lower bound for the exact configured objective across that region.
    A null bound leaves the region unresolved. Visibility, grouping, and
    variance stability are never inferred by this controller.

    The callback is a trusted mathematical contract. Only scoped results can
    prune a region or close a continuous-objective gap. Every incumbent is
    evaluated through the common CandidateScorer.
    """
    if not isinstance(scorer, CandidateScorer):
        if not all(hasattr(scorer, name) for name in ("config", "measurements", "evaluate")):
            raise TypeError("scorer must expose config, measurements, and evaluate.")
    if not callable(bound_callback):
        raise TypeError("bound_callback must be callable.")
    declared = scorer.config.bounds_xy
    if declared is None:
        raise ValueError("Branch-and-bound requires finite config.bounds_xy.")
    declared = (float(declared[0]), float(declared[1]), float(declared[2]), float(declared[3]))
    _validate_rectangle(declared)
    if max_candidates is None:
        max_candidates = scorer.config.candidate_budget
    if type(max_candidates) is not int or max_candidates < 1:
        raise ValueError("max_candidates must be a positive integer.")
    if max_regions is not None and (type(max_regions) is not int or max_regions < 1):
        raise ValueError("max_regions must be a positive integer or None.")
    if type(max_depth) is not int or max_depth < 0:
        raise ValueError("max_depth must be a nonnegative integer.")
    assumptions_tuple = tuple(sorted((assumptions or {}).items()))

    started = perf_counter()
    outcomes: Counter[CandidateStatus] = Counter()
    incumbent_candidates: list[Hypothesis] = []
    candidate_count = regions_created = regions_processed = regions_pruned = 0
    depth_limited: list[RegionDiagnostic] = []

    def bound_region(rectangle: Rectangle, depth: int) -> _Region:
        context = BranchBoundContext(
            scorer=scorer,
            region_xy_m=rectangle,
            declared_bounds_xy_m=declared,
            depth=depth,
            assumptions=assumptions_tuple,
        )
        result = bound_callback(context)
        if not isinstance(result, BoundsResult):
            raise TypeError("bound_callback must return a BoundsResult.")
        if result.bound is not None:
            if not isfinite(result.bound):
                raise ValueError("A region lower bound must be finite.")
            if result.reason is not None or not result.valid_scope:
                raise ValueError("A finite bound requires valid_scope and no unresolved reason.")
        elif result.reason is None:
            raise ValueError("An unresolved bound must include a reason.")
        return _Region(rectangle, depth, result.bound, result.valid_scope, result.reason)

    queue: list[_Region] = [bound_region(declared, 0)]
    regions_created = 1
    stop_reason = "queue_exhausted"
    exhausted_budget_limit: int | None = None
    exhausted_budget_used: int | None = None
    region_limit = max_regions if max_regions is None or max_regions % 2 == 1 else max_regions - 1
    search_status: Literal["completed", "budget_exhausted"] = "completed"
    certificate = False
    bound_gap: float | None = None

    while queue:
        if candidate_count >= max_candidates:
            search_status, stop_reason = "budget_exhausted", "candidate_budget"
            break

        incumbent = min((item.score for item in incumbent_candidates), default=None)
        if incumbent is not None and all(item.lower_bound is not None for item in queue):
            global_lower = min(item.lower_bound for item in queue if item.lower_bound is not None)
            gap = max(0.0, incumbent - global_lower)
            if gap <= _score_tolerance(scorer, incumbent):
                for index, queued_region in enumerate(queue):
                    if queued_region.sampled:
                        continue
                    if candidate_count >= max_candidates:
                        search_status, stop_reason = "budget_exhausted", "candidate_budget"
                        exhausted_budget_limit = exhausted_budget_used = max_candidates
                        break
                    qxmin, qxmax, qymin, qymax = queued_region.bounds
                    qmidpoint = (qxmin + (qxmax - qxmin) / 2.0, qymin + (qymax - qymin) / 2.0)
                    queued_evaluation = scorer.evaluate(qmidpoint)
                    candidate_count += 1
                    outcomes[queued_evaluation.status] += 1
                    if queued_evaluation.hypothesis is not None:
                        _record_candidate(
                            incumbent_candidates, queued_evaluation.hypothesis, scorer
                        )
                    queue[index] = _Region(
                        queued_region.bounds,
                        queued_region.depth,
                        queued_region.lower_bound,
                        queued_region.valid_scope,
                        queued_region.reason,
                        True,
                    )
                incumbent = min((item.score for item in incumbent_candidates), default=None)
                if search_status == "budget_exhausted":
                    break
                if incumbent is not None:
                    global_lower = min(
                        item.lower_bound for item in queue if item.lower_bound is not None
                    )
                    gap = max(0.0, incumbent - global_lower)
                    if gap <= _score_tolerance(scorer, incumbent):
                        certificate, bound_gap = True, gap
                        stop_reason = "objective_gap_closed"
                        break

        region = queue.pop(0)
        regions_processed += 1
        if region.lower_bound is not None and incumbent is not None:
            tolerance = _score_tolerance(scorer, incumbent)
            margin = max(
                scorer.config.absolute_score_tolerance * 1e-6,
                16.0 * 2.220446049250313e-16 * max(1.0, abs(incumbent), abs(region.lower_bound)),
            )
            if region.lower_bound > incumbent + tolerance + margin:
                regions_pruned += 1
                continue

        xmin, xmax, ymin, ymax = region.bounds
        midpoint = (xmin + (xmax - xmin) / 2.0, ymin + (ymax - ymin) / 2.0)
        if not region.sampled:
            evaluation = scorer.evaluate(midpoint)
            candidate_count += 1
            outcomes[evaluation.status] += 1
            if evaluation.hypothesis is not None:
                _record_candidate(incumbent_candidates, evaluation.hypothesis, scorer)

        if region.depth >= max_depth:
            depth_limited.append(
                RegionDiagnostic(
                    region.bounds,
                    region.depth,
                    region.lower_bound,
                    region.valid_scope,
                    region.reason or "maximum split depth reached before gap closure",
                )
            )
            continue

        children = _split_longest_dimension(region.bounds)
        if children is None:
            depth_limited.append(
                RegionDiagnostic(
                    region.bounds,
                    region.depth,
                    region.lower_bound,
                    region.valid_scope,
                    region.reason or "region cannot be split further in floating-point coordinates",
                )
            )
            continue

        if region_limit is not None and regions_created + 2 > region_limit:
            queue.append(region)
            search_status, stop_reason = "budget_exhausted", "region_budget"
            exhausted_budget_limit = exhausted_budget_used = region_limit
            break
        queue.extend(bound_region(child, region.depth + 1) for child in children)
        regions_created += 2

    if queue and search_status == "completed" and not certificate and not depth_limited:
        stop_reason = "unresolved_queue"
    unresolved = (
        ()
        if certificate
        else tuple(
            RegionDiagnostic(
                item.bounds, item.depth, item.lower_bound, item.valid_scope, item.reason
            )
            for item in sorted(queue, key=lambda item: (item.depth, item.bounds))
        )
        + tuple(depth_limited)
    )

    ranked = tuple(
        sorted(incumbent_candidates, key=lambda item: (item.score, item.east_m, item.north_m))
    )
    is_budgeted = search_status == "budget_exhausted"
    localization = LocalizationResult(
        hypotheses=ranked,
        elapsed_s=perf_counter() - started,
        decision_status="ambiguous" if ranked else "no_fix",
        search_status=search_status,
        certificate_scope="continuous_objective" if certificate else "none",
        report_count=len(scorer.measurements.measurements),
        objective_version=scorer.config.objective_version,
        bound_gap=bound_gap if certificate else None,
        budget_limit=(exhausted_budget_limit or max_candidates) if is_budgeted else None,
        budget_used=(exhausted_budget_used or candidate_count) if is_budgeted else None,
    )
    return BranchBoundResult(
        localization,
        regions_created,
        regions_processed,
        candidate_count,
        outcomes["scored"],
        regions_pruned,
        unresolved,
        stop_reason,
    )


def _record_candidate(candidates: list[Hypothesis], candidate: Hypothesis, scorer: Scorer) -> None:
    best = min((item.score for item in candidates), default=None)
    if best is None or candidate.score < best:
        candidates[:] = [
            item for item in candidates if _scores_tied(item.score, candidate.score, scorer)
        ]
        if not any(_same_pose(item, candidate) for item in candidates):
            candidates.append(candidate)
    elif _scores_tied(best, candidate.score, scorer) and not any(
        _same_pose(item, candidate) for item in candidates
    ):
        candidates.append(candidate)


def _scores_tied(left: float, right: float, scorer: Scorer) -> bool:
    return abs(left - right) <= _score_tolerance(scorer, min(left, right))


def _score_tolerance(scorer: Scorer, score: float) -> float:
    return scorer.config.absolute_score_tolerance + scorer.config.relative_score_tolerance * abs(
        score
    )


def _same_pose(left: Hypothesis, right: Hypothesis) -> bool:
    return left.east_m == right.east_m and left.north_m == right.north_m


def _split_longest_dimension(bounds: Rectangle) -> tuple[Rectangle, Rectangle] | None:
    xmin, xmax, ymin, ymax = bounds
    width, height = xmax - xmin, ymax - ymin
    if width <= 0.0 or height <= 0.0:
        return None
    if width >= height:
        midpoint = xmin + width / 2.0
        if midpoint <= xmin or midpoint >= xmax:
            return None
        return (xmin, midpoint, ymin, ymax), (midpoint, xmax, ymin, ymax)
    midpoint = ymin + height / 2.0
    if midpoint <= ymin or midpoint >= ymax:
        return None
    return (xmin, xmax, ymin, midpoint), (xmin, xmax, midpoint, ymax)


def _validate_rectangle(bounds: Rectangle) -> None:
    xmin, xmax, ymin, ymax = bounds
    if not all(isfinite(value) for value in bounds) or xmin >= xmax or ymin >= ymax:
        raise ValueError("Search rectangles must have finite positive width and height.")


__all__ = [
    "BoundCallback",
    "BranchBoundContext",
    "BranchBoundResult",
    "RegionDiagnostic",
    "Scorer",
    "branch_and_bound_search",
]
