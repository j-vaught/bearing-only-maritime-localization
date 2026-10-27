"""Experimental batched GPU prediction and assignment stage for pair RANSAC.

Map geometry/indexing and angular grouping remain CPU-managed. CuPy handles
candidate/catalog inverse geodesics and batched scoring; CPU FP64 verifies costs
and creates authoritative outcomes.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from math import atan2, degrees, hypot, isfinite
from time import perf_counter
from typing import Any, Literal

import cupy as cp
import numpy as np
from pyproj import Geod, Transformer

from maritime.search.gpu.batch_prediction import predict_candidates_cupy
from maritime.search.gpu.batch_scoring import score_prediction_batch_cupy
from maritime.search.gpu.search_adapter import GpuCandidateScorer, _check_matrix, _to_evaluation
from maritime.search.gpu.shoreline import GpuShorelineIndex
from maritime.search.progress import SearchProgressReporter
from maritime.search.scoring import CandidateEvaluation, CandidateSearchRun, CandidateStatus

_GEOD = Geod(ellps="WGS84")
_STATUS_ORDER: tuple[CandidateStatus, ...] = (
    "scored",
    "invalid_water",
    "unresolved_coverage",
    "unresolved_model",
)


@dataclass(frozen=True, slots=True)
class ExperimentalPipelineResult:
    run: CandidateSearchRun
    stage_seconds: dict[str, float]
    shoreline_cpu_fallbacks: int
    stable_candidate_ids: tuple[int, ...]
    candidate_outcomes: tuple[tuple[int, CandidateStatus], ...]


def score_candidate_positions_experimental(
    scorer: GpuCandidateScorer,
    positions_xy: Sequence[tuple[float, float]],
    *,
    batch_size: int = 8,
    progress: SearchProgressReporter | None = None,
    telemetry: Any | None = None,
    device_id: int | None = None,
) -> ExperimentalPipelineResult:
    """Run the optional batched geometry/LOS/prediction/scoring prototype.

    IDs are stable input-order integers after nine-decimal position deduplication.
    Unsupported outcomes remain in the candidate count and are reported explicitly.
    Physical catalog records, visible-feature offsets, uncertainty, type labels,
    model status and identity limits come from the scorer's prepared context.
    CPU verification checks GPU likelihood matrices and assignment objectives given
    the GPU-produced predictions. It does not independently verify GPU geometry.
    """
    if not isinstance(scorer, GpuCandidateScorer):
        raise TypeError("scorer must be GpuCandidateScorer.")
    if type(batch_size) is not int or batch_size < 1:
        raise ValueError("batch_size must be positive.")
    device = scorer.device_ids[0] if device_id is None else device_id
    if type(device) is not int or device < 0:
        raise ValueError("device_id must be a nonnegative integer.")
    unique: list[tuple[float, float]] = []
    seen: set[tuple[float, float]] = set()
    for item in positions_xy:
        try:
            e, n = item
        except (TypeError, ValueError) as exc:
            raise ValueError("Each candidate position must contain east and north.") from exc
        if not isfinite(e) or not isfinite(n):
            raise ValueError("Candidate coordinates must be finite.")
        key = round(float(e), 9), round(float(n), 9)
        if key not in seen:
            seen.add(key)
            unique.append(key)
    count = len(unique)
    started = perf_counter()
    timings: dict[str, float] = {}
    outcomes: dict[int, CandidateEvaluation] = {}
    if count == 0:
        return ExperimentalPipelineResult(_assemble_run(scorer, (), 0, 0.0), {}, 0, (), ())

    context = scorer._prediction_context
    records = tuple(r for r in context.records if r.is_physical)
    keys = tuple(r.record.key for r in records)
    types = tuple(r.record.normalized_type or r.record.raw_type for r in records)
    lonlat: list[tuple[float, float]] = []
    target_xy: list[tuple[float, float]] = []
    qualities: list[Literal["calibrated", "nominal_zero_unknown", "unsupported"]] = []
    unsupported: list[str] = []
    identity_limited: list[str] = []
    uncertainty: list[float] = []
    widths: list[float] = []
    for item in records:
        record = item.record
        lon, lat = record.longitude_deg, record.latitude_deg
        if item.offset_quality == "calibrated" and (item.east_offset_m or item.north_offset_m):
            azimuth = degrees(atan2(item.east_offset_m, item.north_offset_m)) % 360.0
            lon, lat, _ = _GEOD.fwd(
                lon, lat, azimuth, hypot(item.east_offset_m, item.north_offset_m)
            )
        lonlat.append((float(lon), float(lat)))
        qualities.append(item.offset_quality)
        if item.offset_quality == "unsupported":
            unsupported.append(record.key)
        if item.identity_limited:
            identity_limited.append(record.key)
        uncertainty.append(item.uncertainty_m if item.uncertainty_m is not None else np.nan)
        widths.append(item.half_width_deg)
    forward = Transformer.from_crs("EPSG:4326", scorer.vector_map.crs, always_xy=True)
    tx, ty = forward.transform([p[0] for p in lonlat], [p[1] for p in lonlat])
    target_xy = list(zip(map(float, tx), map(float, ty), strict=True))
    inverse = context.inverse

    # CPU map indexing/setup stays explicit; shoreline queries are delegated to the prototype.
    t = perf_counter()
    shoreline = GpuShorelineIndex(scorer.vector_map, cupy_module=cp)
    timings["shoreline_index_setup"] = perf_counter() - t
    for start in range(0, count, batch_size):
        stop = min(start + batch_size, count)
        batch_positions = unique[start:stop]
        batch_ids = tuple(range(start, stop))
        t = perf_counter()
        cand_lon, cand_lat = inverse.transform(
            [p[0] for p in batch_positions], [p[1] for p in batch_positions]
        )
        candidates_ll = np.column_stack((cand_lon, cand_lat))
        preliminary = predict_candidates_cupy(
            candidates_ll,
            lonlat,
            candidate_ids=batch_ids,
            object_keys=keys,
            object_types=types,
            visibility_state=cp.ones((len(batch_positions), len(records)), dtype=cp.uint8),
            visibility_radius_m=scorer.config.max_range_m,
            minimum_range_m=scorer.config.min_range_m,
            range_inclusive=scorer.config.range_inclusive,
            angular_resolution_deg=scorer.config.circular_merge_tolerance_deg,
            type_labels_enabled=scorer.config.use_types,
            angular_half_width_deg=widths,
            catalog_uncertainty_m=uncertainty,
            unsupported_keys=unsupported,
            identity_limited_keys=identity_limited,
            offset_quality=qualities,
            cupy_module=cp,
        )
        in_range = cp.asnumpy(preliminary.in_range)
        timings["gpu_range_prediction"] = (
            timings.get("gpu_range_prediction", 0.0) + perf_counter() - t
        )
        if progress is not None:
            progress("experimental_gpu_range_prediction", stop, count)
        t = perf_counter()
        water = shoreline.classify_candidates(batch_positions, device_id=device)
        vis: Any = shoreline.visibility_matrix(
            batch_positions,
            target_xy,
            in_range_mask=in_range,
            device_id=device,
            return_device_array=True,
        )
        cp.cuda.get_current_stream().synchronize()
        timings["gpu_shoreline"] = timings.get("gpu_shoreline", 0.0) + perf_counter() - t
        if progress is not None:
            progress("experimental_gpu_shoreline", stop, count)
        t = perf_counter()
        predictions = predict_candidates_cupy(
            candidates_ll,
            lonlat,
            candidate_ids=batch_ids,
            object_keys=keys,
            object_types=types,
            visibility_state=vis,
            visibility_radius_m=scorer.config.max_range_m,
            minimum_range_m=scorer.config.min_range_m,
            range_inclusive=scorer.config.range_inclusive,
            angular_resolution_deg=scorer.config.circular_merge_tolerance_deg,
            type_labels_enabled=scorer.config.use_types,
            angular_half_width_deg=widths,
            catalog_uncertainty_m=uncertainty,
            observer_status=water,
            unsupported_keys=unsupported,
            identity_limited_keys=identity_limited,
            offset_quality=qualities,
            cupy_module=cp,
        )
        timings["gpu_prediction_grouping"] = (
            timings.get("gpu_prediction_grouping", 0.0) + perf_counter() - t
        )
        if progress is not None:
            progress("experimental_gpu_grouping", stop, count)
        resolved_ids = []
        resolved_predictions = []
        for local, candidate_id in enumerate(batch_ids):
            prediction = predictions.groups[local]
            if water[local] == "land":
                outcomes[candidate_id] = CandidateEvaluation(
                    "invalid_water", None, None, None, "candidate is on land"
                )
            elif water[local] == "unknown":
                outcomes[candidate_id] = CandidateEvaluation(
                    "unresolved_coverage", None, prediction, None, "candidate water status unknown"
                )
            elif prediction.unresolved_model:
                outcomes[candidate_id] = CandidateEvaluation(
                    "unresolved_model", None, prediction, None, "unsupported feature model"
                )
            elif prediction.coverage_status != "known":
                outcomes[candidate_id] = CandidateEvaluation(
                    "unresolved_coverage", None, prediction, None, "unknown visibility coverage"
                )
            elif scorer.config.objective_version == "legacy_corrected_v0":
                outcomes[candidate_id] = scorer._cpu_score_prepared(
                    batch_positions[local], prediction
                )
            else:
                resolved_ids.append(candidate_id)
                resolved_predictions.append(prediction)
        if resolved_ids:
            t = perf_counter()
            gpu_costs, assignment = score_prediction_batch_cupy(
                scorer.measurements, resolved_predictions, scorer.config, cupy_module=cp
            )
            for local_index, (candidate_id, prediction) in enumerate(
                zip(resolved_ids, resolved_predictions, strict=True)
            ):
                cpu_costs = scorer._cpu_costs(prediction)
                _check_matrix(
                    _cost_slice(gpu_costs, local_index, cpu_costs),
                    cpu_costs,
                    scorer.config,
                )
                evaluation = _to_evaluation(unique[candidate_id], prediction, cpu_costs)
                if evaluation.hypothesis is None:
                    raise RuntimeError("GPU candidate evaluation is unexpectedly unscored.")
                device_cost = float(assignment.device_costs[local_index].item())
                if not np.isclose(
                    device_cost,
                    evaluation.hypothesis.score,
                    rtol=scorer.config.relative_score_tolerance,
                    atol=scorer.config.absolute_score_tolerance,
                ):
                    raise RuntimeError("GPU assignment objective differs from CPU authority.")
                outcomes[candidate_id] = evaluation
            timings["gpu_scoring_and_cpu_verification"] = (
                timings.get("gpu_scoring_and_cpu_verification", 0.0) + perf_counter() - t
            )
        if progress is not None:
            progress("experimental_gpu_scoring", stop, count)

    ordered = tuple(outcomes[i] for i in range(count))
    if telemetry is not None:
        tally = Counter(e.status for e in ordered)
        telemetry.record_candidates(generated=count, evaluated=count, retained=tally["scored"])
    result = _assemble_run(scorer, ordered, count, perf_counter() - started)
    return ExperimentalPipelineResult(
        run=result,
        stage_seconds=dict(timings),
        shoreline_cpu_fallbacks=shoreline.visibility_cpu_fallback_count
        + shoreline.point_cpu_fallback_count,
        stable_candidate_ids=tuple(range(count)),
        candidate_outcomes=tuple((i, outcomes[i].status) for i in range(count)),
    )


def _cost_slice(costs: Any, index: int, cpu_costs: Any) -> Any:
    """Materialize one padded GPU cost slice for comparison to the CPU oracle."""
    from maritime.search.likelihood import LikelihoodCostMatrix

    nr, ng = cpu_costs.matched_costs.shape
    return LikelihoodCostMatrix(
        matched_costs=cp.asnumpy(costs.matched_costs[index, :nr, :ng]),
        report_clutter_costs=cp.asnumpy(costs.report_clutter_costs[index, :nr]),
        group_miss_costs=cp.asnumpy(costs.group_miss_costs[index, :ng]),
        residuals_deg=cp.asnumpy(costs.residuals_deg[index, :nr, :ng]),
        baseline_constant=float(costs.baseline_constants[index].item()),
    )


def _assemble_run(
    scorer: GpuCandidateScorer,
    evaluations: Sequence[CandidateEvaluation],
    count: int,
    elapsed: float,
) -> CandidateSearchRun:
    from maritime.schemas.localization import LocalizationResult

    tally = Counter(e.status for e in evaluations)
    hypotheses = tuple(
        sorted(
            (e.hypothesis for e in evaluations if e.hypothesis is not None), key=lambda h: h.score
        )
    )
    cert = "none"
    result = LocalizationResult(
        hypotheses=hypotheses,
        elapsed_s=elapsed,
        decision_status="ambiguous" if hypotheses else "no_fix",
        search_status="completed",
        certificate_scope=cert,
        budget_limit=count,
        budget_used=count,
        report_count=len(scorer.measurements.measurements),
        objective_version=scorer.config.objective_version,
    )
    return CandidateSearchRun(
        localization=result,
        candidate_count=count,
        outcome_counts=tuple((s, tally[s]) for s in _STATUS_ORDER),
    )


__all__ = ["ExperimentalPipelineResult", "score_candidate_positions_experimental"]
