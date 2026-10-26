"""Common exact scorer for fixed geographic observer candidates."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from math import atan2, degrees, isfinite
from time import perf_counter
from typing import Literal

from maritime_localization_shared.contracts.inputs_v2 import CatalogV2, MeasurementSetV2
from maritime_localization_shared.contracts.localization import (
    Assignment,
    Hypothesis,
    LocalizationResult,
    ScoreComponents,
)
from maritime_localization_shared.geospatial.spatial_index import GeographicSpatialIndex
from maritime_localization_shared.geospatial.vector_map import VectorMap

from .assignment import assign_likelihood_costs
from .config import SearchConfig
from .likelihood import LikelihoodCostMatrix, build_likelihood_costs
from .prediction import PredictedGroupSet, predict_visible_groups

CandidateStatus = Literal["scored", "invalid_water", "unresolved_coverage", "unresolved_model"]
_STATUS_ORDER: tuple[CandidateStatus, ...] = (
    "scored",
    "invalid_water",
    "unresolved_coverage",
    "unresolved_model",
)


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    """Candidate outcome; only fully supported candidates have a hypothesis."""

    status: CandidateStatus
    hypothesis: Hypothesis | None
    predicted: PredictedGroupSet | None
    costs: LikelihoodCostMatrix | None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.status == "scored":
            if self.hypothesis is None or self.predicted is None or self.costs is None:
                raise ValueError(
                    "A scored candidate requires a complete hypothesis and score inputs."
                )
        elif self.hypothesis is not None:
            raise ValueError("An unsupported candidate cannot carry a finite hypothesis.")


@dataclass(frozen=True, slots=True)
class CandidateSearchRun:
    """Ranked candidate results and explicit counts for non-scored outcomes."""

    localization: LocalizationResult
    candidate_count: int
    outcome_counts: tuple[tuple[CandidateStatus, int], ...]

    @property
    def hypotheses(self) -> tuple[Hypothesis, ...]:
        """Return ranked, complete scored candidates."""
        return self.localization.hypotheses


class CandidateScorer:
    """Predict visible catalog groups and score one projected position exactly.

    The catalog's geographic index is built once and reused for all candidates.
    Unknown water/line-of-sight coverage and unsupported feature models produce
    explicit outcomes without a score, so incomplete geometry cannot be accepted.
    """

    def __init__(
        self,
        measurements: MeasurementSetV2,
        catalog: CatalogV2,
        vector_map: VectorMap,
        config: SearchConfig,
    ) -> None:
        if not isinstance(measurements, MeasurementSetV2):
            raise TypeError("measurements must be a MeasurementSetV2 instance.")
        if not isinstance(catalog, CatalogV2):
            raise TypeError("catalog must be a CatalogV2 instance.")
        if not isinstance(vector_map, VectorMap):
            raise TypeError("vector_map must be a VectorMap instance.")
        if not isinstance(config, SearchConfig):
            raise TypeError("config must be a SearchConfig instance.")
        self.measurements = measurements
        self.catalog = catalog
        self.vector_map = vector_map
        self.config = config
        self._spatial_index = GeographicSpatialIndex(
            ((record.longitude_deg, record.latitude_deg) for record in catalog.records),
            keys=(record.key for record in catalog.records),
        )

    def evaluate(self, position_xy: tuple[float, float]) -> CandidateEvaluation:
        """Return a complete scored hypothesis or an explicit unresolved outcome."""
        try:
            east_m, north_m = position_xy
        except (TypeError, ValueError) as exc:
            raise ValueError("position_xy must contain two finite coordinates.") from exc
        if not isfinite(east_m) or not isfinite(north_m):
            raise ValueError("position_xy must contain two finite coordinates.")
        position = (float(east_m), float(north_m))
        water = self.vector_map.point_water_status(position)
        if water == "land":
            return CandidateEvaluation("invalid_water", None, None, None, "candidate is on land")
        if water == "unknown":
            return CandidateEvaluation(
                "unresolved_coverage", None, None, None, "candidate water status is unknown"
            )

        predicted = predict_visible_groups(
            position,
            self.catalog,
            observer_crs=self.vector_map.crs,
            visibility_radius_m=self.config.max_range_m,
            vector_map=self.vector_map,
            minimum_range_m=self.config.min_range_m,
            range_inclusive=self.config.range_inclusive,
            angular_resolution_deg=self.config.circular_merge_tolerance_deg,
            type_labels_enabled=self.config.use_types,
            spatial_index=self._spatial_index,
        )
        if predicted.unresolved_model:
            return CandidateEvaluation(
                "unresolved_model",
                None,
                predicted,
                None,
                "one or more catalog feature models are unsupported",
            )
        if predicted.coverage_status != "known":
            return CandidateEvaluation(
                "unresolved_coverage",
                None,
                predicted,
                None,
                "candidate visibility intersects unknown map coverage",
            )

        groups = predicted.groups
        costs = build_likelihood_costs(
            self.measurements,
            [group.bearing_deg for group in groups],
            [group.object_type for group in groups],
            self.config,
            [degrees(atan2(group.catalog_uncertainty_m or 0.0, group.range_m)) for group in groups],
        )
        assignment = assign_likelihood_costs(costs)
        components = ScoreComponents(
            matched_cost=assignment.matched_cost,
            clutter_cost=assignment.baseline_constant + assignment.unmatched_report_cost,
            missed_cost=assignment.unused_group_cost,
            total_cost=assignment.cost,
        )
        hypothesis = Hypothesis(
            east_m=position[0],
            north_m=position[1],
            score=assignment.cost,
            assignments=tuple(
                Assignment(
                    report_index=report_index,
                    catalog_key=groups[group_index].representative_key,
                    residual_deg=float(costs.residuals_deg[report_index, group_index]),
                    cost=float(costs.matched_costs[report_index, group_index]),
                )
                for report_index, group_index in assignment.matches
            ),
            score_components=components,
        )
        return CandidateEvaluation("scored", hypothesis, predicted, costs)

    __call__ = evaluate


class CandidateSearchAccumulator:
    """Evaluate unique fixed positions once and assemble their honest result."""

    def __init__(self, scorer: CandidateScorer) -> None:
        if not isinstance(scorer, CandidateScorer):
            raise TypeError("scorer must be a CandidateScorer instance.")
        self.scorer = scorer
        self.started = perf_counter()
        self.seen: set[tuple[float, float]] = set()
        self.hypotheses: list[Hypothesis] = []
        self.scored_by_position: dict[tuple[float, float], Hypothesis] = {}
        self.counts: Counter[CandidateStatus] = Counter()
        self.candidate_count = 0

    @staticmethod
    def position_key(position_xy: tuple[float, float]) -> tuple[float, float]:
        east_m, north_m = position_xy
        if not isfinite(east_m) or not isfinite(north_m):
            raise ValueError("candidate positions must contain two finite coordinates.")
        return round(float(east_m), 9), round(float(north_m), 9)

    def add(self, position_xy: tuple[float, float]) -> CandidateEvaluation | None:
        key = self.position_key(position_xy)
        if key in self.seen:
            return None
        self.seen.add(key)
        evaluation = self.scorer.evaluate(key)
        self.candidate_count += 1
        self.counts[evaluation.status] += 1
        if evaluation.hypothesis is not None:
            self.hypotheses.append(evaluation.hypothesis)
            self.scored_by_position[key] = evaluation.hypothesis
        return evaluation

    def is_new(self, position_xy: tuple[float, float]) -> bool:
        return self.position_key(position_xy) not in self.seen

    def finish(
        self,
        *,
        search_status: Literal["completed", "budget_exhausted"] = "completed",
        budget_limit: int | None = None,
        budget_used: int | None = None,
        certificate_scope: Literal["none", "finite_candidate_set"] = "none",
    ) -> CandidateSearchRun:
        if search_status == "budget_exhausted":
            if budget_limit is None or budget_used != budget_limit:
                raise ValueError("budget_exhausted requires budget_used equal to budget_limit.")
        elif (budget_limit is None) != (budget_used is None):
            raise ValueError("budget_limit and budget_used must be provided together.")
        ranked = tuple(sorted(self.hypotheses, key=lambda item: item.score))
        supported_set = self.candidate_count > 0 and (
            self.counts["scored"] + self.counts["invalid_water"] == self.candidate_count
        )
        effective_certificate = (
            certificate_scope
            if (
                certificate_scope == "finite_candidate_set"
                and supported_set
                and search_status == "completed"
            )
            else "none"
        )
        result = LocalizationResult(
            hypotheses=ranked,
            elapsed_s=perf_counter() - self.started,
            decision_status="ambiguous" if ranked else "no_fix",
            search_status=search_status,
            certificate_scope=effective_certificate,
            budget_limit=budget_limit,
            budget_used=budget_used,
            report_count=len(self.scorer.measurements.measurements),
            objective_version=self.scorer.config.objective_version,
        )
        return CandidateSearchRun(
            localization=result,
            candidate_count=self.candidate_count,
            outcome_counts=tuple((status, self.counts[status]) for status in _STATUS_ORDER),
        )


def score_candidate_positions(
    scorer: CandidateScorer,
    positions_xy: Iterable[tuple[float, float]],
    *,
    search_status: Literal["completed", "budget_exhausted"] = "completed",
    budget_limit: int | None = None,
    budget_used: int | None = None,
    candidate_limit: int | None = None,
    certificate_scope: Literal["none", "finite_candidate_set"] = "none",
) -> CandidateSearchRun:
    """Score a candidate stream and report no global-optimality certificate.

    The caller owns candidate generation and declares whether it exhausted its
    stream or stopped at budget. A candidate_limit bounds consumption and turns
    a longer stream into an explicit budget_exhausted result. Heuristic methods
    never accept a finite candidate minimum.
    """
    accumulator = CandidateSearchAccumulator(scorer)
    iterator = iter(positions_xy)
    if candidate_limit is not None:
        if type(candidate_limit) is not int or candidate_limit < 1:
            raise ValueError("candidate_limit must be a positive integer.")
        for position in iterator:
            if accumulator.candidate_count >= candidate_limit and accumulator.is_new(position):
                search_status = "budget_exhausted"
                budget_limit = candidate_limit
                budget_used = candidate_limit
                break
            accumulator.add(position)
        if search_status == "completed" and budget_limit is None:
            budget_limit = candidate_limit
            budget_used = accumulator.candidate_count
    else:
        for position in iterator:
            accumulator.add(position)
    return accumulator.finish(
        search_status=search_status,
        budget_limit=budget_limit,
        budget_used=budget_used,
        certificate_scope=certificate_scope,
    )


__all__ = [
    "CandidateEvaluation",
    "CandidateScorer",
    "CandidateSearchRun",
    "CandidateSearchAccumulator",
    "CandidateStatus",
    "score_candidate_positions",
]
