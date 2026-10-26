from __future__ import annotations

from dataclasses import dataclass
from math import atan2, degrees, isfinite
from typing import Literal

from maritime_localization_shared.contracts.inputs_v2 import CatalogV2, MeasurementSetV2
from maritime_localization_shared.contracts.localization import (
    Assignment,
    Hypothesis,
    ScoreComponents,
)
from maritime_localization_shared.geospatial.spatial_index import GeographicSpatialIndex
from maritime_localization_shared.geospatial.vector_map import VectorMap

from .assignment import assign_likelihood_costs
from .config import SearchConfig
from .likelihood import LikelihoodCostMatrix, build_likelihood_costs
from .prediction import PredictedGroupSet, predict_visible_groups

CandidateStatus = Literal["scored", "invalid_water", "unresolved_coverage", "unresolved_model"]


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
                "one or more nearby catalog feature models are unsupported",
            )
        if predicted.coverage_status != "known":
            return CandidateEvaluation(
                "unresolved_coverage",
                None,
                predicted,
                None,
                "candidate visibility intersects unknown map coverage",
            )

        groups = tuple(
            group for group in predicted.groups if group.range_m >= self.config.min_range_m
        )
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


__all__ = ["CandidateEvaluation", "CandidateScorer", "CandidateStatus"]
