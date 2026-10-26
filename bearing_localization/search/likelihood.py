"""Versioned bearing likelihood costs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import cos, exp, isfinite, log, pi, sqrt
from typing import TypeAlias

import numpy as np

from maritime_localization_shared.contracts.inputs_v2 import MeasurementSetV2
from maritime_localization_shared.contracts.object_types import normalize_object_type

from .config import SearchConfig

PredictedType: TypeAlias = str | int | Mapping[str | int, float] | None


@dataclass(frozen=True, slots=True)
class LikelihoodCostMatrix:
    """Matched matrix, report clutter/group miss costs, and baseline Lambda."""

    matched_costs: np.ndarray
    report_clutter_costs: np.ndarray
    group_miss_costs: np.ndarray
    baseline_constant: float
    residuals_deg: np.ndarray

    def total_for_assignment(self, matches: Sequence[tuple[int, int]]) -> float:
        """Compute full objective for zero-based injective report/group pairs."""
        nr, ng = self.matched_costs.shape
        used_r: set[int] = set()
        used_g: set[int] = set()
        score = self.baseline_constant
        for i, j in matches:
            if type(i) is not int or type(j) is not int or not 0 <= i < nr or not 0 <= j < ng:
                raise ValueError("Assignment indices must identify existing reports and groups.")
            if i in used_r or j in used_g:
                raise ValueError("Assignment must be injective.")
            edge = float(self.matched_costs[i, j])
            if not isfinite(edge):
                return float("inf")
            score += edge
            used_r.add(i)
            used_g.add(j)
        score += sum(float(self.report_clutter_costs[i]) for i in range(nr) if i not in used_r)
        score += sum(float(self.group_miss_costs[j]) for j in range(ng) if j not in used_g)
        return float(score)


def build_likelihood_costs(
    measurements: MeasurementSetV2 | Sequence[float] | np.ndarray,
    predicted_bearings_deg: Sequence[float] | np.ndarray,
    predicted_types: Sequence[PredictedType] | None,
    config: SearchConfig,
    predicted_uncertainty_deg: Sequence[float] | np.ndarray | None = None,
    *,
    observation_types: Sequence[str | int | None] | None = None,
    observation_sigmas_deg: Sequence[float | None] | np.ndarray | None = None,
) -> LikelihoodCostMatrix:
    """Build report×group costs; all bearing and uncertainty values use degrees.

    Inputs may be MeasurementSetV2 or a bearing array with parallel optional
    types and sigmas. Predicted bearings represent already-merged physical
    groups. Predicted types accept hard labels or soft probability mappings.
    Extent is ignored. Independent uncertainties combine in quadrature and are
    floored. Diagonal covariance supplies per-report sigma and must agree with
    explicit sigmas. Non-diagonal covariance and nonzero common bias are rejected.
    """
    if not isinstance(config, SearchConfig):
        raise TypeError("config must be SearchConfig.")
    if config.common_bearing_bias_prior_deg is not None:
        raise NotImplementedError("Calibrated common-bearing-bias integration is unsupported.")
    if isinstance(measurements, MeasurementSetV2):
        if observation_types is not None or observation_sigmas_deg is not None:
            raise ValueError("Do not override MeasurementSetV2 metadata.")
        if measurements.calibrated_common_bias_sigma_deg not in (
            None,
            0.0,
        ) or measurements.calibrated_common_bias_mean_deg not in (None, 0.0):
            raise NotImplementedError("Calibrated common-bearing-bias integration is unsupported.")
        obs = np.asarray([x.bearing_deg for x in measurements.measurements], dtype=float)
        obs_types = [x.object_type for x in measurements.measurements]
        raw_sigmas = [x.sigma_deg for x in measurements.measurements]
        if measurements.covariance_deg2 is not None:
            covariance = np.asarray(measurements.covariance_deg2, dtype=float)
            if not np.allclose(covariance, np.diag(np.diag(covariance)), rtol=0.0, atol=1e-12):
                raise NotImplementedError("Non-diagonal covariance requires a joint likelihood.")
            diagonal_sigmas = np.sqrt(np.maximum(np.diag(covariance), 0.0))
            for i, sigma_value in enumerate(raw_sigmas):
                if sigma_value is not None and not np.isclose(
                    sigma_value, diagonal_sigmas[i], rtol=1e-8, atol=1e-12
                ):
                    raise ValueError("Per-measurement sigma must agree with diagonal covariance.")
                raw_sigmas[i] = float(diagonal_sigmas[i])
    else:
        obs = np.asarray(measurements, dtype=float)
        if obs.ndim != 1 or not np.isfinite(obs).all() or np.any((obs < 0) | (obs >= 360)):
            raise ValueError("Observation bearings must be finite and in [0, 360).")
        obs_types = list(observation_types) if observation_types is not None else [None] * len(obs)
        raw_sigmas = (
            list(observation_sigmas_deg)
            if observation_sigmas_deg is not None
            else [None] * len(obs)
        )
    pred = np.asarray(predicted_bearings_deg, dtype=float)
    if pred.ndim != 1 or not np.isfinite(pred).all() or np.any((pred < 0) | (pred >= 360)):
        raise ValueError("Predicted bearings must be finite and in [0, 360).")
    nr, ng = len(obs), len(pred)
    if len(obs_types) != nr or len(raw_sigmas) != nr:
        raise ValueError("Observation metadata lengths must match reports.")
    pred_types = list(predicted_types) if predicted_types is not None else [None] * ng
    if len(pred_types) != ng:
        raise ValueError("predicted_types length must match predicted bearings.")
    if predicted_uncertainty_deg is None:
        pred_sigmas = np.zeros(ng)
    else:
        pred_sigmas = np.asarray(predicted_uncertainty_deg, dtype=float)
        if pred_sigmas.ndim == 0:
            pred_sigmas = np.full(ng, float(pred_sigmas))
        if (
            pred_sigmas.shape != (ng,)
            or not np.isfinite(pred_sigmas).all()
            or np.any(pred_sigmas < 0)
        ):
            raise ValueError("Predicted uncertainty must be finite, nonnegative, and match groups.")
    report_sigmas = np.empty(nr)
    for i, raw in enumerate(raw_sigmas):
        s = config.generated_sigma_deg if raw is None else float(raw)
        if not isfinite(s) or s < 0:
            raise ValueError("Observation sigmas must be finite and nonnegative.")
        report_sigmas[i] = s
    sigma = np.maximum(
        np.hypot(report_sigmas[:, None], pred_sigmas[None, :]), config.sigma_floor_deg
    )
    residual = np.asarray(wrap_bearing_deg(pred[None, :] - obs[:, None]), dtype=float)
    if config.objective_version == "legacy_corrected_v0":
        # Historical corrected loss: abs(wrapped residual)/each report sigma;
        # unmatched cost is 12 degrees divided by that sigma. No miss/base term.
        return LikelihoodCostMatrix(
            np.abs(residual) / sigma,
            12.0 / np.maximum(report_sigmas, config.sigma_floor_deg),
            np.zeros(ng),
            0.0,
            residual,
        )
    obs_kinds = [normalize_object_type(x) for x in obs_types]
    dists = [_normalize_predicted_type(x) for x in pred_types]
    matched = np.empty((nr, ng))
    for i in range(nr):
        for j in range(ng):
            fb = _wrapped_gaussian_mixture(
                float(residual[i, j]), float(sigma[i, j]), config.bearing_outlier_probability
            )
            q = 1.0
            report_kind = obs_kinds[i]
            distribution = dists[j]
            if (
                config.use_types
                and report_kind not in (None, "unknown")
                and distribution is not None
            ):
                if isinstance(pred_types[j], Mapping):
                    # Explicit soft labels are calibrated probabilities. A
                    # zero compatible mass is an impossible edge, not a log clamp.
                    q = sum(p for k, p in distribution.items() if k == report_kind)
                else:
                    q = sum(
                        p * _type_probability(report_kind, k, config.correct_type_probability)
                        for k, p in distribution.items()
                    )
            likelihood = config.detection_probability * fb * q
            matched[i, j] = -log(likelihood) if likelihood > 0 else float("inf")
    intensity = config.expected_unmatched_reports / 360.0
    clutter = -log(intensity) if intensity > 0 else float("inf")
    return LikelihoodCostMatrix(
        matched,
        np.full(nr, clutter),
        np.full(ng, -log(1.0 - config.detection_probability)),
        config.expected_unmatched_reports,
        residual,
    )


def wrap_bearing_deg(angle: float | np.ndarray) -> float | np.ndarray:
    """Wrap angular differences to [-180, 180)."""
    out = (np.asarray(angle, dtype=float) + 180) % 360 - 180
    return float(out) if out.ndim == 0 else out


def _wrapped_gaussian_mixture(residual: float, sigma: float, epsilon: float) -> float:
    """Degree^-1 normalized wrapped Gaussian mixed with uniform outliers."""
    if sigma <= 15:
        radius = max(1, int(np.ceil(9 * sigma / 360)) + 1)
        density = sum(
            exp(-0.5 * ((residual + 360 * k) / sigma) ** 2) for k in range(-radius, radius + 1)
        ) / (sqrt(2 * pi) * sigma)
    else:
        density = 1 / 360
        for n in range(1, 50):
            a = exp(-0.5 * (2 * pi * n * sigma / 360) ** 2)
            if a < 1e-16:
                break
            density += 2 / 360 * a * cos(2 * pi * n * residual / 360)
    return (1 - epsilon) * density + epsilon / 360


def _normalize_predicted_type(value: PredictedType) -> dict[str, float] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        if not value:
            raise ValueError("Predicted type distribution cannot be empty.")
        out: dict[str, float] = {}
        for label, raw in value.items():
            p = float(raw)
            if not isfinite(p) or p < 0:
                raise ValueError("Predicted type probabilities must be finite and nonnegative.")
            kind = normalize_object_type(label)
            if kind not in (None, "unknown") and p > 0:
                out[kind] = out.get(kind, 0) + p
        total = sum(out.values())
        if total == 0:
            return None
        if abs(total - 1) > 1e-8:
            raise ValueError("Predicted type probabilities must sum to one.")
        return {k: p / total for k, p in out.items()}
    kind = normalize_object_type(value)
    return None if kind in (None, "unknown") else {kind: 1.0}


def _type_probability(report: str, predicted: str, p_correct: float) -> float:
    """Symmetric confusion model over four neutral coarse types."""
    labels = ("vessel", "physical_aid", "shoreline", "virtual_aid")
    return p_correct if report == predicted else (1 - p_correct) / (len(labels) - 1)


__all__ = ["LikelihoodCostMatrix", "PredictedType", "build_likelihood_costs", "wrap_bearing_deg"]
