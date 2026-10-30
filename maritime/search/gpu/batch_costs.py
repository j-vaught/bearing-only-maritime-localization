"""Batched device likelihood matrices for predicted visible groups."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from importlib import import_module
from math import atan2, degrees
from typing import Any

import numpy as np

from maritime.schemas.inputs_v2 import MeasurementSetV2
from maritime.search.config import SearchConfig
from maritime.search.gpu.costs import (
    _observation_metadata,
    _type_probabilities,
    _wrapped_density_cupy,
)
from maritime.search.prediction import PredictedGroupSet


@dataclass(frozen=True, slots=True)
class BatchedLikelihoodCosts:
    """Device-resident padded costs; counts identify each valid group prefix."""

    matched_costs: Any
    report_clutter_costs: Any
    group_miss_costs: Any
    baseline_constants: Any
    residuals_deg: Any
    group_counts: Any


def build_batched_likelihood_costs_cupy(
    measurements: MeasurementSetV2,
    predictions: Sequence[PredictedGroupSet],
    config: SearchConfig,
    *,
    cupy_module: Any | None = None,
) -> BatchedLikelihoodCosts:
    """Evaluate the existing objective on one padded batch of predicted groups.

    Prediction records and categorical type metadata are prepared on the host.
    All report-by-group likelihood calculations and returned costs stay on device.
    Padding is marked by group_counts and has no effect on assignment.
    """
    cp = cupy_module if cupy_module is not None else import_module("cupy")
    if config.common_bearing_bias_prior_deg is not None:
        raise NotImplementedError("Common bearing bias is not supported.")
    obs, obs_types, sigmas = _observation_metadata(measurements, config, None, None)
    batch = len(predictions)
    counts = np.asarray([len(pred.groups) for pred in predictions], dtype=np.int64)
    max_groups = int(counts.max()) if batch else 0
    bearings = np.zeros((batch, max_groups), dtype=np.float64)
    uncertainties = np.zeros((batch, max_groups), dtype=np.float64)
    type_probs = np.ones((batch, len(obs), max_groups), dtype=np.float64)
    for i, pred in enumerate(predictions):
        if pred.coverage_status != "known" or pred.unresolved_model:
            raise ValueError("Only resolved predictions may be scored.")
        groups = pred.groups
        n = len(groups)
        bearings[i, :n] = [g.bearing_deg for g in groups]
        uncertainties[i, :n] = [
            degrees(atan2(g.catalog_uncertainty_m or 0.0, g.range_m)) for g in groups
        ]
        type_probs[i, :, :n] = _type_probabilities(
            obs_types, [g.object_type for g in groups], config
        )
    if not np.isfinite(bearings).all() or not np.isfinite(uncertainties).all():
        raise ValueError("Predicted bearings and uncertainty must be finite.")
    device_obs = cp.asarray(obs, dtype=cp.float64)
    device_bearings = cp.asarray(bearings, dtype=cp.float64)
    sigma = cp.maximum(
        cp.hypot(cp.asarray(sigmas)[None, :, None], cp.asarray(uncertainties)[:, None, :]),
        config.sigma_floor_deg,
    )
    residuals = (device_bearings[:, None, :] - device_obs[None, :, None] + 180.0) % 360.0 - 180.0
    if config.objective_version == "legacy_corrected_v0":
        matched = cp.abs(residuals) / sigma
        report_clutter = cp.broadcast_to(
            12.0 / cp.maximum(cp.asarray(sigmas), config.sigma_floor_deg), (batch, len(obs))
        )
        group_miss = cp.zeros((batch, max_groups), dtype=cp.float64)
        baseline = cp.zeros(batch, dtype=cp.float64)
    else:
        density = _wrapped_density_cupy(cp, residuals, sigma, config.bearing_outlier_probability)
        likelihood = config.detection_probability * density * cp.asarray(type_probs)
        matched = cp.where(likelihood > 0.0, -cp.log(likelihood), cp.inf)
        intensity = config.expected_unmatched_reports / 360.0
        clutter = -np.log(intensity) if intensity > 0.0 else float("inf")
        report_clutter = cp.full((batch, len(obs)), clutter, dtype=cp.float64)
        group_miss = cp.full((batch, max_groups), -np.log1p(-config.detection_probability))
        baseline = cp.full(batch, config.expected_unmatched_reports, dtype=cp.float64)
    return BatchedLikelihoodCosts(
        matched, report_clutter, group_miss, baseline, residuals, cp.asarray(counts)
    )
