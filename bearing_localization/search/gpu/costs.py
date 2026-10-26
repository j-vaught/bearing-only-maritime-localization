"""Bounded CuPy cost generation for CPU-prepared true-north bearings."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from importlib import import_module
from math import isfinite
from typing import Any

import numpy as np

from maritime_localization_shared.contracts.inputs_v2 import MeasurementSetV2
from maritime_localization_shared.contracts.object_types import normalize_object_type

from ..assignment import assign_likelihood_costs
from ..config import SearchConfig
from ..likelihood import (
    LikelihoodCostMatrix,
    PredictedType,
    _normalize_predicted_type,
    _type_probability,
    build_likelihood_costs,
    wrap_bearing_deg,
)


@dataclass(frozen=True, slots=True)
class CandidateBearingInput:
    """One candidate's CPU-prepared WGS84 true-north visible-group bearings."""

    candidate_id: str | int
    predicted_bearings_deg: Sequence[float] | np.ndarray
    predicted_types: Sequence[PredictedType] | None = None
    predicted_uncertainty_deg: Sequence[float] | np.ndarray | None = None


@dataclass(frozen=True, slots=True)
class CandidateLikelihoodCosts:
    """Candidate costs and CPU-authoritative values for close batch winners."""

    candidate_id: str | int
    costs: LikelihoodCostMatrix
    gpu_assignment_cost: float
    cpu_assignment_cost: float | None
    max_cpu_matrix_error: float | None
    cpu_verified: bool


def build_likelihood_costs_cupy(
    measurements: MeasurementSetV2 | Sequence[float] | np.ndarray,
    predicted_bearings_deg: Sequence[float] | np.ndarray,
    predicted_types: Sequence[PredictedType] | None,
    config: SearchConfig,
    predicted_uncertainty_deg: Sequence[float] | np.ndarray | None = None,
    *,
    observation_types: Sequence[str | int | None] | None = None,
    observation_sigmas_deg: Sequence[float | None] | np.ndarray | None = None,
    max_pairs_per_chunk: int = 65_536,
    cupy_module: Any | None = None,
    telemetry: Any | None = None,
) -> LikelihoodCostMatrix:
    """Build the A09 likelihood matrix with bounded CuPy pair tiles.

    Geometry and angular grouping remain CPU responsibilities. Inputs match
    build_likelihood_costs. Bearings and metadata are prepared in CPU FP64;
    CuPy computes wrapped densities and matched NLLs in bounded chunks. The
    optional iterator CPU-reranks GPU batch winners within the configured score
    tolerance. CuPy is imported lazily.
    """
    if not isinstance(config, SearchConfig):
        raise TypeError("config must be SearchConfig.")
    if type(max_pairs_per_chunk) is not int or max_pairs_per_chunk < 1:
        raise ValueError("max_pairs_per_chunk must be a positive integer.")
    if config.objective_version == "legacy_corrected_v0":
        return build_likelihood_costs(
            measurements,
            predicted_bearings_deg,
            predicted_types,
            config,
            predicted_uncertainty_deg,
            observation_types=observation_types,
            observation_sigmas_deg=observation_sigmas_deg,
        )
    if config.common_bearing_bias_prior_deg is not None:
        raise NotImplementedError("Calibrated common-bearing-bias integration is unsupported.")
    cp = cupy_module if cupy_module is not None else import_module("cupy")
    observations, obs_types, report_sigmas = _observation_metadata(
        measurements, config, observation_types, observation_sigmas_deg
    )
    pred = np.asarray(predicted_bearings_deg, dtype=np.float64)
    if pred.ndim != 1 or not np.isfinite(pred).all() or np.any((pred < 0.0) | (pred >= 360.0)):
        raise ValueError("Predicted bearings must be finite and in [0, 360).")
    pred_types = list(predicted_types) if predicted_types is not None else [None] * len(pred)
    if len(pred_types) != len(pred):
        raise ValueError("predicted_types length must match predicted bearings.")
    for value in pred_types:
        _normalize_predicted_type(value)
    pred_sigmas = _predicted_sigmas(predicted_uncertainty_deg, len(pred))
    sigma = np.maximum(
        np.hypot(report_sigmas[:, None], pred_sigmas[None, :]), config.sigma_floor_deg
    )
    q = _type_probabilities(obs_types, pred_types, config)
    residual_matrix = np.asarray(wrap_bearing_deg(pred[None, :] - observations[:, None]))
    residual = residual_matrix.reshape(-1)
    flat_sigma, flat_q = sigma.reshape(-1), q.reshape(-1)
    matched = np.empty((len(observations), len(pred)), dtype=np.float64)
    flat_output = matched.reshape(-1)
    workspace_size = min(max_pairs_per_chunk, len(residual))
    if workspace_size:
        workspace_sigma = cp.empty(workspace_size, dtype=cp.float64)
        workspace_q = cp.empty(workspace_size, dtype=cp.float64)
        workspace_residual = cp.empty(workspace_size, dtype=cp.float64)
        for start in range(0, len(residual), max_pairs_per_chunk):
            stop = min(start + max_pairs_per_chunk, len(residual))
            count = stop - start
            device_id = int(cp.cuda.runtime.getDevice())
            ds, dq, dr = (workspace_sigma[:count], workspace_q[:count], workspace_residual[:count])
            with _gpu_stage(telemetry, "cost_transfer_in", device_id):
                ds.set(flat_sigma[start:stop])
                dq.set(flat_q[start:stop])
                dr.set(residual[start:stop])
            if telemetry is not None:
                telemetry.record_transfer(count * 3 * 8, "host_to_device", device_id)
            with _gpu_stage(telemetry, "cost_kernel", device_id):
                density = _wrapped_density_cupy(cp, dr, ds, config.bearing_outlier_probability)
                likelihood = config.detection_probability * density * dq
                device_cost = cp.where(likelihood > 0.0, -cp.log(likelihood), cp.inf)
                cp.cuda.get_current_stream().synchronize()
            with _gpu_stage(telemetry, "cost_transfer_out", device_id):
                flat_output[start:stop] = cp.asnumpy(device_cost)
            if telemetry is not None:
                telemetry.record_transfer(count * 8, "device_to_host", device_id)
            del ds, dq, dr, density, likelihood, device_cost
        del workspace_sigma, workspace_q, workspace_residual
    intensity = config.expected_unmatched_reports / 360.0
    clutter_cost = -np.log(intensity) if intensity > 0.0 else float("inf")
    group_miss_cost = -np.log(1.0 - config.detection_probability)
    return LikelihoodCostMatrix(
        matched,
        np.full(len(observations), clutter_cost, dtype=np.float64),
        np.full(len(pred), group_miss_cost, dtype=np.float64),
        config.expected_unmatched_reports,
        residual_matrix,
    )


def iter_candidate_likelihood_costs_cupy(
    measurements: MeasurementSetV2 | Sequence[float] | np.ndarray,
    candidates: Iterator[CandidateBearingInput] | Sequence[CandidateBearingInput],
    config: SearchConfig,
    *,
    batch_size: int = 8,
    max_pairs_per_chunk: int = 65_536,
    close_score_tolerance: float | None = None,
    observation_types: Sequence[str | int | None] | None = None,
    observation_sigmas_deg: Sequence[float | None] | np.ndarray | None = None,
    cupy_module: Any | None = None,
    telemetry: Any | None = None,
    cpu_verify_all: bool = False,
) -> Iterator[CandidateLikelihoodCosts]:
    """Lazily score candidate batches and CPU-rerank GPU scores near each batch best.

    Candidate bearings must come from CPU geodesic prediction. Only a bounded
    batch of candidate records is retained; no candidate×report×catalog tensor
    is built. Close GPU winners are rerun through the A09 FP64 reference. GPU
    proximity is a screening heuristic, not a certificate: callers must set
    cpu_verify_all for final-retained/acceptance candidates or verify those
    candidates separately. cpu_verified marks every returned record explicitly.
    """
    if type(batch_size) is not int or batch_size < 1:
        raise ValueError("batch_size must be a positive integer.")
    if type(cpu_verify_all) is not bool:
        raise TypeError("cpu_verify_all must be bool.")
    tolerance = (
        config.absolute_score_tolerance if close_score_tolerance is None else close_score_tolerance
    )
    if not isfinite(tolerance) or tolerance < 0.0:
        raise ValueError("close_score_tolerance must be finite and nonnegative.")
    source = iter(candidates)
    while True:
        batch: list[CandidateBearingInput] = []
        for _ in range(batch_size):
            try:
                candidate = next(source)
            except StopIteration:
                break
            if not isinstance(candidate, CandidateBearingInput):
                raise TypeError("candidates must contain CandidateBearingInput records.")
            batch.append(candidate)
        if not batch:
            return
        scored: list[tuple[CandidateBearingInput, LikelihoodCostMatrix, float]] = []
        for candidate in batch:
            costs = build_likelihood_costs_cupy(
                measurements,
                candidate.predicted_bearings_deg,
                candidate.predicted_types,
                config,
                candidate.predicted_uncertainty_deg,
                observation_types=observation_types,
                observation_sigmas_deg=observation_sigmas_deg,
                max_pairs_per_chunk=max_pairs_per_chunk,
                cupy_module=cupy_module,
            )
            scored.append((candidate, costs, assign_likelihood_costs(costs).cost))
        best = min(score for _, _, score in scored)
        for candidate, costs, score in scored:
            cpu_cost = None
            matrix_error = None
            if cpu_verify_all or score - best <= tolerance + config.relative_score_tolerance * abs(
                best
            ):
                exact = build_likelihood_costs(
                    measurements,
                    candidate.predicted_bearings_deg,
                    candidate.predicted_types,
                    config,
                    candidate.predicted_uncertainty_deg,
                    observation_types=observation_types,
                    observation_sigmas_deg=observation_sigmas_deg,
                )
                cpu_cost = assign_likelihood_costs(exact).cost
                finite = np.isfinite(exact.matched_costs) & np.isfinite(costs.matched_costs)
                matrix_error = (
                    float(np.max(np.abs(exact.matched_costs[finite] - costs.matched_costs[finite])))
                    if finite.any()
                    else 0.0
                )
                costs = exact
            yield CandidateLikelihoodCosts(
                candidate.candidate_id, costs, score, cpu_cost, matrix_error, cpu_cost is not None
            )


def _observation_metadata(
    measurements: MeasurementSetV2 | Sequence[float] | np.ndarray,
    config: SearchConfig,
    observation_types: Sequence[str | int | None] | None,
    observation_sigmas_deg: Sequence[float | None] | np.ndarray | None,
) -> tuple[np.ndarray, list[str | int | None], np.ndarray]:
    if isinstance(measurements, MeasurementSetV2):
        if observation_types is not None or observation_sigmas_deg is not None:
            raise ValueError("Do not override MeasurementSetV2 metadata.")
        if measurements.calibrated_common_bias_sigma_deg not in (
            None,
            0.0,
        ) or measurements.calibrated_common_bias_mean_deg not in (None, 0.0):
            raise NotImplementedError("Calibrated common-bearing-bias integration is unsupported.")
        observations = np.asarray(
            [item.bearing_deg for item in measurements.measurements], dtype=np.float64
        )
        kinds: list[str | int | None] = [item.object_type for item in measurements.measurements]
        raw = [item.sigma_deg for item in measurements.measurements]
        if measurements.covariance_deg2 is not None:
            covariance = np.asarray(measurements.covariance_deg2, dtype=np.float64)
            if not np.allclose(covariance, np.diag(np.diag(covariance)), rtol=0.0, atol=1e-12):
                raise NotImplementedError("Non-diagonal covariance requires a joint likelihood.")
            diagonal = np.sqrt(np.maximum(np.diag(covariance), 0.0))
            for i, value in enumerate(raw):
                if value is not None and not np.isclose(value, diagonal[i], rtol=1e-8, atol=1e-12):
                    raise ValueError("Per-measurement sigma must agree with diagonal covariance.")
                raw[i] = float(diagonal[i])
    else:
        observations = np.asarray(measurements, dtype=np.float64)
        if (
            observations.ndim != 1
            or not np.isfinite(observations).all()
            or np.any((observations < 0.0) | (observations >= 360.0))
        ):
            raise ValueError("Observation bearings must be finite and in [0, 360).")
        count = len(observations)
        kinds = list(observation_types) if observation_types is not None else [None] * count
        raw = list(observation_sigmas_deg) if observation_sigmas_deg is not None else [None] * count
    if len(kinds) != len(observations) or len(raw) != len(observations):
        raise ValueError("Observation metadata lengths must match reports.")
    sigmas = np.asarray(
        [config.generated_sigma_deg if value is None else float(value) for value in raw],
        dtype=np.float64,
    )
    if not np.isfinite(sigmas).all() or np.any(sigmas < 0.0):
        raise ValueError("Observation sigmas must be finite and nonnegative.")
    return observations, kinds, sigmas


def _predicted_sigmas(values: Sequence[float] | np.ndarray | None, count: int) -> np.ndarray:
    if values is None:
        return np.zeros(count, dtype=np.float64)
    sigmas = np.asarray(values, dtype=np.float64)
    if sigmas.ndim == 0:
        sigmas = np.full(count, float(sigmas), dtype=np.float64)
    if sigmas.shape != (count,) or not np.isfinite(sigmas).all() or np.any(sigmas < 0.0):
        raise ValueError("Predicted uncertainty must be finite, nonnegative, and match groups.")
    return sigmas


def _type_probabilities(
    observations: Sequence[str | int | None],
    predictions: Sequence[PredictedType],
    config: SearchConfig,
) -> np.ndarray:
    obs = [normalize_object_type(value) for value in observations]
    distributions = [_normalize_predicted_type(value) for value in predictions]
    result = np.ones((len(obs), len(distributions)), dtype=np.float64)
    for i, report in enumerate(obs):
        for j, distribution in enumerate(distributions):
            if not config.use_types or report in (None, "unknown") or distribution is None:
                continue
            if isinstance(predictions[j], Mapping):
                result[i, j] = sum(p for kind, p in distribution.items() if kind == report)
            else:
                result[i, j] = sum(
                    p * _type_probability(report, kind, config.correct_type_probability)
                    for kind, p in distribution.items()
                )
    return result


def _gpu_stage(telemetry: Any | None, name: str, device_id: int) -> Any:
    if telemetry is None:
        from contextlib import nullcontext

        return nullcontext()
    return telemetry.stage(name, gpu=True, device_id=device_id)


def _wrapped_density_cupy(cp: Any, residual: Any, sigma: Any, epsilon: float) -> Any:
    """Vectorized normalized wrapped Gaussian mixture matching A09's regimes."""
    narrow = sigma <= 15.0
    narrow_density = cp.zeros_like(sigma)
    max_sigma = float(cp.max(sigma).item()) if sigma.size else 0.0
    radius = max(1, int(np.ceil(9.0 * min(max_sigma, 15.0) / 360.0)) + 1)
    for winding in range(-radius, radius + 1):
        z = (residual + 360.0 * winding) / sigma
        narrow_density += cp.exp(-0.5 * z * z)
    narrow_density /= cp.sqrt(2.0 * np.pi) * sigma
    wide_density = cp.full_like(sigma, 1.0 / 360.0)
    for harmonic in range(1, 50):
        amplitude = cp.exp(-0.5 * (2.0 * np.pi * harmonic * sigma / 360.0) ** 2)
        wide_density += (
            (2.0 / 360.0) * amplitude * cp.cos(2.0 * np.pi * harmonic * residual / 360.0)
        )
        if float(cp.max(amplitude).item()) < 1e-16:
            break
    density = cp.where(narrow, narrow_density, wide_density)
    return (1.0 - epsilon) * density + epsilon / 360.0


__all__ = [
    "CandidateBearingInput",
    "CandidateLikelihoodCosts",
    "build_likelihood_costs_cupy",
    "iter_candidate_likelihood_costs_cupy",
]
