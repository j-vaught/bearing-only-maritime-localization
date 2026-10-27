"""Combine batched GPU likelihood costs and optional one-to-one assignment."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from maritime.schemas.inputs_v2 import MeasurementSetV2
from maritime.search.config import SearchConfig
from maritime.search.gpu.batch_assignment import (
    BatchedAssignmentResult,
    assign_partial_cost_matrices_rawkernel,
)
from maritime.search.gpu.batch_costs import (
    BatchedLikelihoodCosts,
    build_batched_likelihood_costs_cupy,
)
from maritime.search.prediction import PredictedGroupSet


def score_prediction_batch_cupy(
    measurements: MeasurementSetV2,
    predictions: Sequence[PredictedGroupSet],
    config: SearchConfig,
    *,
    cupy_module: Any | None = None,
) -> tuple[BatchedLikelihoodCosts, BatchedAssignmentResult]:
    """Score resolved predictions with the exact partial-assignment objective.

    The caller is responsible for retaining candidate IDs and rejecting or
    resolving unknown map coverage and unsupported feature models.
    """
    costs = build_batched_likelihood_costs_cupy(
        measurements, predictions, config, cupy_module=cupy_module
    )
    assignment = assign_partial_cost_matrices_rawkernel(
        costs.matched_costs,
        costs.report_clutter_costs,
        costs.group_miss_costs,
        baseline_constants=costs.baseline_constants,
        group_counts=costs.group_counts,
        cupy_module=cupy_module,
    )
    return costs, assignment
