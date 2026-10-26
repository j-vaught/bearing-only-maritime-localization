"""CPU-authoritative end-to-end CuPy scorer adapter for version-2 search."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from math import atan2, degrees, isfinite
from typing import Any

import numpy as np

from maritime_localization_shared.contracts.inputs_v2 import CatalogV2, MeasurementSetV2
from maritime_localization_shared.contracts.localization import (
    Assignment,
    Hypothesis,
    ScoreComponents,
)
from maritime_localization_shared.geospatial.vector_map import VectorMap

from ..assignment import assign_likelihood_costs
from ..config import SearchConfig
from ..likelihood import LikelihoodCostMatrix, build_likelihood_costs
from ..prediction import PredictedGroupSet, predict_visible_groups
from ..scoring import CandidateEvaluation, CandidateScorer
from ..telemetry import SearchTelemetry
from .costs import CandidateBearingInput
from .runtime import iter_candidate_likelihood_costs_multi_gpu


class GpuCandidateScorer(CandidateScorer):
    """Drop-in scorer. Geometry stays CPU geodesic; A27 costs run on named GPUs."""

    def __init__(
        self,
        measurements: MeasurementSetV2,
        catalog: CatalogV2,
        vector_map: VectorMap,
        config: SearchConfig,
        *,
        device_ids: Sequence[int],
        telemetry: SearchTelemetry | None = None,
        fallback_on_error: bool = False,
        batch_size: int = 8,
        max_pairs_per_chunk: int = 65_536,
        max_in_flight_chunks: int | None = None,
    ) -> None:
        super().__init__(measurements, catalog, vector_map, config)
        devices = tuple(device_ids)
        if not devices or any(type(d) is not int or d < 0 for d in devices):
            raise ValueError("device_ids must contain one or more nonnegative integers.")
        if len(set(devices)) != len(devices):
            raise ValueError("device_ids must be unique.")
        if telemetry is not None and not isinstance(telemetry, SearchTelemetry):
            raise TypeError("telemetry must be SearchTelemetry or None.")
        if type(fallback_on_error) is not bool:
            raise TypeError("fallback_on_error must be bool.")
        if type(batch_size) is not int or batch_size < 1:
            raise ValueError("batch_size must be positive.")
        if type(max_pairs_per_chunk) is not int or max_pairs_per_chunk < 1:
            raise ValueError("max_pairs_per_chunk must be positive.")
        if max_in_flight_chunks is not None and (
            type(max_in_flight_chunks) is not int or max_in_flight_chunks < len(devices)
        ):
            raise ValueError("max_in_flight_chunks must be at least the device count.")
        self.device_ids = devices
        self.telemetry = telemetry
        self.fallback_on_error = fallback_on_error
        self.batch_size = batch_size
        self.max_pairs_per_chunk = max_pairs_per_chunk
        self.max_in_flight_chunks = max_in_flight_chunks
        self._gpu_disabled = False
        self._next_device = 0

    def evaluate(self, position_xy: tuple[float, float]) -> CandidateEvaluation:
        """Synchronous API used by existing algorithms; device selection is round-robin."""
        device = self.device_ids[self._next_device % len(self.device_ids)]
        self._next_device += 1
        return self.evaluate_many((position_xy,), _device_ids=(device,))[0]

    def evaluate_many(
        self,
        positions_xy: Iterable[tuple[float, float]],
        *,
        _device_ids: Sequence[int] | None = None,
        _candidate_id_start: int = 0,
    ) -> tuple[CandidateEvaluation, ...]:
        """Batch a fixed candidate stream through A30; preserve every input and order.

        A30 IDs are monotonically increasing input indices. The return tuple is
        exactly position-for-position with positions_xy, including unsupported outcomes.
        """
        if type(_candidate_id_start) is not int or _candidate_id_start < 0:
            raise ValueError("_candidate_id_start must be a nonnegative integer.")
        positions = tuple(positions_xy)
        if not positions:
            return ()
        if self._gpu_disabled:
            return tuple(self._cpu_evaluate(_valid_position(p)) for p in positions)
        prepared: list[tuple[int, tuple[float, float], PredictedGroupSet]] = []
        outcomes: dict[int, CandidateEvaluation] = {}
        for offset, raw_position in enumerate(positions):
            index = _candidate_id_start + offset
            position = _valid_position(raw_position)
            with self._stage("gpu_cpu_geometry"):
                water = self.vector_map.point_water_status(position)
                if water == "land":
                    outcomes[index] = CandidateEvaluation(
                        "invalid_water", None, None, None, "candidate is on land"
                    )
                    continue
                if water == "unknown":
                    outcomes[index] = CandidateEvaluation(
                        "unresolved_coverage", None, None, None, "candidate water status is unknown"
                    )
                    continue
                pred = predict_visible_groups(
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
                    prepared_context=self._prediction_context,
                )
            if pred.unresolved_model:
                outcomes[index] = CandidateEvaluation(
                    "unresolved_model", None, pred, None, "unsupported feature model"
                )
            elif pred.coverage_status != "known":
                outcomes[index] = CandidateEvaluation(
                    "unresolved_coverage", None, pred, None, "unknown visibility coverage"
                )
            else:
                prepared.append((index, position, pred))
        if not prepared:
            return tuple(outcomes[_candidate_id_start + i] for i in range(len(positions)))
        # A27 intentionally keeps the historical objective on the same CPU adapter.
        if self.config.objective_version == "legacy_corrected_v0":
            if self.telemetry is not None:
                self.telemetry.record_unresolved_region("gpu_objective_unsupported")
            for i, pos, pred in prepared:
                outcomes[i] = self._cpu_score_prepared(pos, pred)
            return tuple(outcomes[_candidate_id_start + i] for i in range(len(positions)))

        try:
            inputs = (
                CandidateBearingInput(
                    candidate_id=i,
                    predicted_bearings_deg=tuple(g.bearing_deg for g in pred.groups),
                    predicted_types=tuple(g.object_type for g in pred.groups),
                    predicted_uncertainty_deg=tuple(
                        degrees(atan2(g.catalog_uncertainty_m or 0.0, g.range_m))
                        for g in pred.groups
                    ),
                )
                for i, _, pred in prepared
            )
            with self._stage("gpu_cost_and_cpu_verify"):
                results = tuple(
                    iter_candidate_likelihood_costs_multi_gpu(
                        self.measurements,
                        inputs,
                        self.config,
                        device_ids=tuple(_device_ids)
                        if _device_ids is not None
                        else self.device_ids,
                        batch_size=self.batch_size,
                        max_in_flight_chunks=self.max_in_flight_chunks,
                        max_pairs_per_chunk=self.max_pairs_per_chunk,
                        telemetry=self.telemetry,
                    )
                )
            expected = tuple(i for i, _, _ in prepared)
            if tuple(r.candidate_id for r in results) != expected:
                raise RuntimeError("GPU scorer lost, duplicated, or reordered candidate IDs.")
            if any(not r.cpu_verified or r.cpu_assignment_cost is None for r in results):
                raise RuntimeError("GPU result lacks CPU FP64 verification.")
            for (i, pos, pred), result in zip(prepared, results, strict=True):
                with self._stage("gpu_cpu_authority"):
                    exact = self._cpu_costs(pred)
                    _check_matrix(result.costs, exact, self.config)
                    outcomes[i] = _to_evaluation(pos, pred, exact)
        except BaseException as exc:
            if self.telemetry is not None:
                self.telemetry.record_exception(exc, stage="gpu_search_adapter")
            if not self.fallback_on_error:
                raise
            self._gpu_disabled = True
            if self.telemetry is not None:
                self.telemetry.record_unresolved_region("gpu_cpu_fallback")
            for i, pos, _ in prepared:
                outcomes[i] = self._cpu_evaluate(pos)
        return tuple(outcomes[_candidate_id_start + i] for i in range(len(positions)))

    def _cpu_evaluate(self, position: tuple[float, float]) -> CandidateEvaluation:
        with self._stage("gpu_cpu_fallback"):
            return super().evaluate(position)

    def _cpu_score_prepared(
        self, position: tuple[float, float], pred: PredictedGroupSet
    ) -> CandidateEvaluation:
        with self._stage("gpu_cpu_fallback"):
            return _to_evaluation(position, pred, self._cpu_costs(pred))

    def _cpu_costs(self, pred: PredictedGroupSet) -> LikelihoodCostMatrix:
        groups = pred.groups
        return build_likelihood_costs(
            self.measurements,
            [g.bearing_deg for g in groups],
            [g.object_type for g in groups],
            self.config,
            [degrees(atan2(g.catalog_uncertainty_m or 0.0, g.range_m)) for g in groups],
        )

    def _stage(self, name: str) -> Any:
        return self.telemetry.stage(name) if self.telemetry is not None else _NullStage()

    def __call__(self, position_xy: tuple[float, float]) -> CandidateEvaluation:
        return self.evaluate(position_xy)


def _valid_position(position: tuple[float, float]) -> tuple[float, float]:
    try:
        east, north = position
    except (TypeError, ValueError) as exc:
        raise ValueError("position_xy must contain two finite coordinates.") from exc
    if not isfinite(east) or not isfinite(north):
        raise ValueError("position_xy must contain two finite coordinates.")
    return float(east), float(north)


def _check_matrix(
    actual: LikelihoodCostMatrix, expected: LikelihoodCostMatrix, config: SearchConfig
) -> None:
    for name in ("matched_costs", "report_clutter_costs", "group_miss_costs", "residuals_deg"):
        left = np.asarray(getattr(actual, name), dtype=np.float64)
        right = np.asarray(getattr(expected, name), dtype=np.float64)
        if left.shape != right.shape or not np.array_equal(np.isposinf(left), np.isposinf(right)):
            raise RuntimeError(f"GPU/CPU {name} shape or infinity-mask mismatch.")
        finite = np.isfinite(left) & np.isfinite(right)
        if finite.any() and not np.allclose(
            left[finite],
            right[finite],
            rtol=config.relative_score_tolerance,
            atol=config.absolute_score_tolerance,
        ):
            raise RuntimeError(f"GPU/CPU {name} differs beyond the configured tolerance.")


def _to_evaluation(
    position: tuple[float, float],
    pred: PredictedGroupSet,
    costs: LikelihoodCostMatrix,
) -> CandidateEvaluation:
    assignment = assign_likelihood_costs(costs)
    groups = pred.groups
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
                report_index=i,
                catalog_key=groups[j].representative_key,
                residual_deg=float(costs.residuals_deg[i, j]),
                cost=float(costs.matched_costs[i, j]),
            )
            for i, j in assignment.matches
        ),
        score_components=components,
    )
    return CandidateEvaluation("scored", hypothesis, pred, costs)


class _NullStage:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        return False


__all__ = ["GpuCandidateScorer"]


def score_candidate_positions_batched(
    scorer: GpuCandidateScorer,
    positions_xy: Iterable[tuple[float, float]],
    *,
    batch_size: int = 8,
    candidate_limit: int | None = None,
    search_status: str = "completed",
    budget_limit: int | None = None,
    budget_used: int | None = None,
    certificate_scope: str = "none",
) -> Any:
    """Run a fixed candidate traversal in bounded GPU batches.

    Candidate generation and budget semantics match score_candidate_positions:
    duplicate positions are evaluated once, a candidate limit is detected only
    when one additional unique candidate exists, and no candidate is dropped.
    """
    from collections import Counter
    from time import perf_counter

    from maritime_localization_shared.contracts.localization import LocalizationResult

    from ..scoring import CandidateSearchRun, CandidateStatus

    if not isinstance(scorer, GpuCandidateScorer):
        raise TypeError("scorer must be GpuCandidateScorer.")
    if type(batch_size) is not int or batch_size < 1:
        raise ValueError("batch_size must be positive.")
    if candidate_limit is not None and (type(candidate_limit) is not int or candidate_limit < 1):
        raise ValueError("candidate_limit must be positive.")
    if search_status not in ("completed", "budget_exhausted"):
        raise ValueError("unsupported search_status.")
    if certificate_scope not in ("none", "finite_candidate_set"):
        raise ValueError("unsupported certificate_scope.")
    started = perf_counter()
    seen: set[tuple[float, float]] = set()
    hypotheses: list[Hypothesis] = []
    counts: Counter[CandidateStatus] = Counter()
    candidate_count = 0
    batch: list[tuple[float, float]] = []

    def flush() -> None:
        nonlocal candidate_count
        if not batch:
            return
        first_id = candidate_count
        evaluations = scorer.evaluate_many(tuple(batch), _candidate_id_start=first_id)
        for evaluation in evaluations:
            counts[evaluation.status] += 1
            if evaluation.hypothesis is not None:
                hypotheses.append(evaluation.hypothesis)
        candidate_count += len(batch)
        batch.clear()

    for raw_position in positions_xy:
        position = _valid_position(raw_position)
        key = round(position[0], 9), round(position[1], 9)
        if key in seen:
            continue
        if candidate_limit is not None and candidate_count + len(batch) >= candidate_limit:
            flush()
            search_status = "budget_exhausted"
            budget_limit = candidate_limit
            budget_used = candidate_limit
            break
        seen.add(key)
        batch.append(key)
        if len(batch) >= batch_size:
            flush()
    flush()
    if search_status == "budget_exhausted" and (
        budget_limit is None or budget_used != budget_limit
    ):
        raise ValueError("budget_exhausted requires budget_used equal to budget_limit.")
    if search_status == "completed" and (budget_limit is None) != (budget_used is None):
        raise ValueError("budget_limit and budget_used must be provided together.")
    if candidate_limit is not None and search_status == "completed" and budget_limit is None:
        budget_limit = candidate_limit
        budget_used = candidate_count
    ranked = tuple(sorted(hypotheses, key=lambda item: item.score))
    supported = (
        candidate_count > 0 and counts["scored"] + counts["invalid_water"] == candidate_count
    )
    effective_certificate = (
        certificate_scope
        if certificate_scope == "finite_candidate_set"
        and supported
        and search_status == "completed"
        else "none"
    )
    result = LocalizationResult(
        hypotheses=ranked,
        elapsed_s=perf_counter() - started,
        decision_status="ambiguous" if ranked else "no_fix",
        search_status=search_status,
        certificate_scope=effective_certificate,
        budget_limit=budget_limit,
        budget_used=budget_used,
        report_count=len(scorer.measurements.measurements),
        objective_version=scorer.config.objective_version,
    )
    status_order: tuple[CandidateStatus, ...] = (
        "scored",
        "invalid_water",
        "unresolved_coverage",
        "unresolved_model",
    )
    return CandidateSearchRun(
        localization=result,
        candidate_count=candidate_count,
        outcome_counts=tuple((status, counts[status]) for status in status_order),
    )
