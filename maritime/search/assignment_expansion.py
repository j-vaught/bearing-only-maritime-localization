"""Bounded single-addition assignment-conditioned bearing proposals.

This module explores one unmatched report and one unused physical catalog
record at a time. Every fitted pose is re-evaluated by the complete canonical
scorer. The finite local proposal window and single-addition branches are an
explicit heuristic scope and never imply a completeness certificate.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from math import atan2, degrees, hypot, isfinite
from time import perf_counter
from typing import Any, Literal

import numpy as np
from pyproj import Geod, Transformer
from scipy.optimize import least_squares

from maritime.schemas.inputs_v2 import CatalogRecordV2
from maritime.schemas.localization import Hypothesis

from .assigned_bearing_refinement import _target_lonlat
from .scoring import CandidateEvaluation, CandidateScorer

_GEOD = Geod(ellps="WGS84")

ExpansionStatus = Literal[
    "completed",
    "no_unmatched_reports",
    "budget_exhausted",
    "invalid_bounds",
    "invalid_seed",
    "stale_hypothesis",
    "invalid_water",
    "unresolved_coverage",
    "unresolved_model",
    "unsupported_calibration",
    "unsupported_covariance",
    "insufficient_assignments",
    "invalid_assignment",
]


@dataclass(frozen=True, slots=True)
class ExpansionConfig:
    """Numerical bounds for one single-addition expansion proposal."""

    local_radius_m: float = 1_000.0
    max_nfev: int = 100
    max_branches: int = 5_000

    def __post_init__(self) -> None:
        if not isfinite(self.local_radius_m) or self.local_radius_m <= 0.0:
            raise ValueError("local_radius_m must be finite and positive.")
        if type(self.max_nfev) is not int or not 1 <= self.max_nfev <= 100:
            raise ValueError("max_nfev must be an integer from one through 100.")
        if type(self.max_branches) is not int or not 1 <= self.max_branches <= 5_000:
            raise ValueError("max_branches must be an integer from one through 5000.")


@dataclass(frozen=True, slots=True)
class ExpansionResult:
    """Serializable result of the bounded assignment expansion."""

    seed: Hypothesis
    best_hypothesis: Hypothesis
    status: ExpansionStatus
    completed: bool
    budget_exhausted: bool
    counters: dict[str, Any]
    branch_events: tuple[dict[str, Any], ...]
    elapsed_s: float
    stage_timings: dict[str, Any] | None = None

    @property
    def improved(self) -> bool:
        return self.best_hypothesis.score < self.seed.score

    @property
    def exact_hypothesis(self) -> Hypothesis:
        return self.best_hypothesis

    @property
    def original_score(self) -> float:
        return self.seed.score

    @property
    def best_score(self) -> float:
        return self.best_hypothesis.score


@dataclass(frozen=True, slots=True)
class _Candidate:
    report_index: int
    record: CatalogRecordV2
    offset_quality: str
    uncertainty_m: float | None


@dataclass(frozen=True, slots=True)
class _FrozenBearing:
    report_index: int
    record: CatalogRecordV2
    target_lonlat: tuple[float, float]
    sigma_deg: float


@dataclass(frozen=True, slots=True)
class _PreparedSeed:
    frozen: tuple[_FrozenBearing, ...]
    records: dict[str, CatalogRecordV2]
    inverse: Transformer
    seed_lonlat: tuple[float, float]


def _result(
    seed: Hypothesis,
    *,
    status: ExpansionStatus,
    completed: bool,
    budget_exhausted: bool = False,
    counters: dict[str, Any] | None = None,
    branch_events: list[dict[str, Any]] | None = None,
    stage_timings: dict[str, Any] | None = None,
    started: float,
) -> ExpansionResult:
    elapsed, finalized_timings = _finalize_stage_timings(stage_timings, started)
    return ExpansionResult(
        seed=seed,
        best_hypothesis=seed,
        status=status,
        completed=completed,
        budget_exhausted=budget_exhausted,
        counters={} if counters is None else dict(counters),
        branch_events=() if branch_events is None else tuple(branch_events),
        elapsed_s=elapsed,
        stage_timings=finalized_timings,
    )


def _new_stage_timings() -> dict[str, Any]:
    """Create opt-in aggregate timing metadata for one expansion mode."""
    return {
        "pool_preparation_wall_s": 0.0,
        "frozen_target_preparation_wall_s": 0.0,
        "solver_fit_wall_s": 0.0,
        "residual_call_count": 0,
        "residual_wall_s": 0.0,
        "canonical_scorer_wall_s": 0.0,
        "seed_validation_wall_s": 0.0,
        "progress_callback_wall_s": 0.0,
        "result_callback_wall_s": 0.0,
        "uninstrumented_remainder_wall_s": 0.0,
    }


def _add_timing(
    stage_timings: dict[str, Any] | None,
    key: str,
    value: float | int,
) -> None:
    if stage_timings is not None:
        stage_timings[key] += value


_ACCOUNTED_TIMING_KEYS = (
    "pool_preparation_wall_s",
    "frozen_target_preparation_wall_s",
    "solver_fit_wall_s",
    "canonical_scorer_wall_s",
    "seed_validation_wall_s",
    "progress_callback_wall_s",
    "result_callback_wall_s",
)


def _finalize_stage_timings(
    stage_timings: dict[str, Any] | None,
    started: float,
) -> tuple[float, dict[str, Any] | None]:
    elapsed = perf_counter() - started
    if stage_timings is None:
        return elapsed, None
    finalized = dict(stage_timings)
    accounted = sum(float(finalized[key]) for key in _ACCOUNTED_TIMING_KEYS)
    finalized["uninstrumented_remainder_wall_s"] = max(0.0, elapsed - accounted)
    return elapsed, finalized


def _timed_callback(
    callback: Callable[[dict[str, Any]], None] | None,
    event: dict[str, Any],
    stage_timings: dict[str, Any] | None,
    key: str,
) -> None:
    if callback is None:
        return
    callback_started = perf_counter() if stage_timings is not None else None
    try:
        callback(dict(event))
    finally:
        if callback_started is not None:
            _add_timing(stage_timings, key, perf_counter() - callback_started)


def _status_for_evaluation(evaluation: CandidateEvaluation) -> ExpansionStatus:
    if evaluation.status == "invalid_water":
        return "invalid_water"
    if evaluation.status == "unresolved_coverage":
        return "unresolved_coverage"
    if evaluation.status == "unresolved_model":
        return "unresolved_model"
    return "unresolved_model"


def _assignment_keys(seed: Hypothesis) -> set[str]:
    return {assignment.catalog_key for assignment in seed.assignments}


def _measurement_sigma(scorer: CandidateScorer, report_index: int) -> float:
    measurement = scorer.measurements.measurements[report_index]
    raw = (
        scorer.config.generated_sigma_deg
        if measurement.sigma_deg is None
        else measurement.sigma_deg
    )
    return float(raw)


def _group_sigma_deg(uncertainty_m: float | None, range_m: float) -> float:
    return degrees(atan2(uncertainty_m or 0.0, range_m))


def _seed_validation(
    scorer: CandidateScorer, seed: Hypothesis
) -> tuple[ExpansionStatus | None, CandidateEvaluation | None]:
    try:
        evaluation = scorer.evaluate((seed.east_m, seed.north_m))
    except (TypeError, ValueError):
        return "invalid_seed", None
    if evaluation.status != "scored" or evaluation.hypothesis is None:
        return _status_for_evaluation(evaluation), evaluation
    exact = evaluation.hypothesis
    if (
        not np.isclose(
            exact.score,
            seed.score,
            rtol=scorer.config.relative_score_tolerance,
            atol=scorer.config.absolute_score_tolerance,
        )
        or exact.assignments != seed.assignments
    ):
        return "stale_hypothesis", evaluation
    return None, evaluation


def _prepare_seed(
    scorer: CandidateScorer,
    seed_evaluation: CandidateEvaluation,
    seed: Hypothesis,
) -> _PreparedSeed:
    predicted = seed_evaluation.predicted
    if predicted is None:
        raise ValueError("A scored seed must include predicted groups.")
    groups = {group.representative_key: group for group in predicted.groups}
    records = {record.key: record for record in scorer.catalog.records}
    frozen: list[_FrozenBearing] = []
    for assignment in seed.assignments:
        group = groups.get(assignment.catalog_key)
        record = records.get(assignment.catalog_key)
        if group is None or record is None:
            raise ValueError(
                f"Seed assignment is not a canonical visible group: {assignment.catalog_key}"
            )
        target = _target_lonlat(record, group.visible_feature_offset_quality)
        sigma = max(
            scorer.config.sigma_floor_deg,
            hypot(
                _measurement_sigma(scorer, assignment.report_index),
                _group_sigma_deg(group.catalog_uncertainty_m, group.range_m),
            ),
        )
        frozen.append(_FrozenBearing(assignment.report_index, record, target, sigma))
    inverse = Transformer.from_crs(scorer.vector_map.crs, "EPSG:4326", always_xy=True)
    seed_lon, seed_lat = inverse.transform(seed.east_m, seed.north_m)
    return _PreparedSeed(tuple(frozen), records, inverse, (float(seed_lon), float(seed_lat)))


def _frozen_for_candidate(
    scorer: CandidateScorer,
    seed_context: _PreparedSeed,
    candidate: _Candidate,
) -> tuple[_FrozenBearing, ...]:
    target = _target_lonlat(candidate.record, candidate.offset_quality)
    distance = _GEOD.inv(
        seed_context.seed_lonlat[0],
        seed_context.seed_lonlat[1],
        target[0],
        target[1],
    )[2]
    sigma = max(
        scorer.config.sigma_floor_deg,
        hypot(
            _measurement_sigma(scorer, candidate.report_index),
            _group_sigma_deg(candidate.uncertainty_m, max(float(distance), 1.0)),
        ),
    )
    return seed_context.frozen + (
        _FrozenBearing(candidate.report_index, candidate.record, target, sigma),
    )


def _fit_branch(
    scorer: CandidateScorer,
    seed: Hypothesis,
    seed_context: _PreparedSeed,
    candidate: _Candidate,
    config: ExpansionConfig,
    *,
    stage_timings: dict[str, Any] | None = None,
) -> tuple[
    str, tuple[float, float] | None, int | None, int, Hypothesis | None, CandidateEvaluation | None
]:
    bounds = scorer.config.bounds_xy
    if bounds is None:
        return "invalid_bounds", None, None, 0, None, None
    if len(seed.assignments) < 2:
        return "insufficient_assignments", None, None, 0, None, None
    frozen_started = perf_counter() if stage_timings is not None else None
    try:
        frozen = _frozen_for_candidate(scorer, seed_context, candidate)
    except ValueError:
        return "invalid_assignment", None, None, 0, None, None
    finally:
        if frozen_started is not None:
            _add_timing(
                stage_timings,
                "frozen_target_preparation_wall_s",
                perf_counter() - frozen_started,
            )
    inverse = seed_context.inverse
    observed = np.asarray(
        [scorer.measurements.measurements[item.report_index].bearing_deg for item in frozen],
        dtype=float,
    )
    sigmas = np.asarray([item.sigma_deg for item in frozen], dtype=float)
    evaluations = 0

    def residuals(position: np.ndarray) -> np.ndarray:
        nonlocal evaluations
        evaluations += 1
        residual_started = perf_counter() if stage_timings is not None else None
        try:
            lon, lat = inverse.transform(float(position[0]), float(position[1]))
            predicted = np.asarray(
                [
                    _GEOD.inv(lon, lat, item.target_lonlat[0], item.target_lonlat[1])[0] % 360.0
                    for item in frozen
                ],
                dtype=float,
            )
            wrapped = (predicted - observed + 180.0) % 360.0 - 180.0
            return wrapped / sigmas
        finally:
            if residual_started is not None:
                _add_timing(stage_timings, "residual_call_count", 1)
                _add_timing(
                    stage_timings,
                    "residual_wall_s",
                    perf_counter() - residual_started,
                )

    xmin, xmax, ymin, ymax = bounds
    lower = np.maximum(
        (xmin, ymin), (seed.east_m - config.local_radius_m, seed.north_m - config.local_radius_m)
    )
    upper = np.minimum(
        (xmax, ymax), (seed.east_m + config.local_radius_m, seed.north_m + config.local_radius_m)
    )
    if np.any(lower >= upper):
        return "invalid_bounds", None, None, evaluations, None, None
    fit_started = perf_counter() if stage_timings is not None else None
    try:
        solve = least_squares(
            residuals,
            np.asarray((seed.east_m, seed.north_m), dtype=float),
            bounds=(lower, upper),
            method="trf",
            max_nfev=config.max_nfev,
        )
    except (TypeError, ValueError, RuntimeError):
        return "solver_failed", None, None, evaluations, None, None
    finally:
        if fit_started is not None:
            _add_timing(stage_timings, "solver_fit_wall_s", perf_counter() - fit_started)
    proposal = (float(solve.x[0]), float(solve.x[1])) if np.isfinite(solve.x).all() else None
    if proposal is None:
        return "solver_failed", None, int(solve.status), evaluations, None, None
    if not solve.success:
        return (
            "max_nfev" if solve.status == 0 else "solver_failed",
            proposal,
            int(solve.status),
            evaluations,
            None,
            None,
        )
    canonical_started = perf_counter() if stage_timings is not None else None
    try:
        evaluation = scorer.evaluate(proposal)
    except (TypeError, ValueError):
        return "solver_failed", proposal, int(solve.status), evaluations, None, None
    finally:
        if canonical_started is not None:
            _add_timing(
                stage_timings,
                "canonical_scorer_wall_s",
                perf_counter() - canonical_started,
            )
    if evaluation.status != "scored" or evaluation.hypothesis is None:
        return (
            _status_for_evaluation(evaluation),
            proposal,
            int(solve.status),
            evaluations,
            None,
            evaluation,
        )
    return "converged", proposal, int(solve.status), evaluations, evaluation.hypothesis, evaluation


def _candidate_pool(
    scorer: CandidateScorer, seed: Hypothesis, config: ExpansionConfig
) -> tuple[list[_Candidate], dict[str, Any], str]:
    context = scorer._prediction_context
    inverse = context.inverse
    lon, lat = inverse.transform(seed.east_m, seed.north_m)
    radius = scorer.config.max_range_m + 2.0 * config.local_radius_m + context.max_offset_m
    indices = scorer._spatial_index.query_radius(float(lon), float(lat), radius)
    assigned = _assignment_keys(seed)
    report_count = len(scorer.measurements.measurements)
    unmatched = [
        index
        for index in range(report_count)
        if index not in {item.report_index for item in seed.assignments}
    ]
    counters = {
        "unmatched_reports": len(unmatched),
        "pool_records": len(indices),
        "eligible_records": 0,
        "nonvisual_records": 0,
        "unsupported_model_records": 0,
        "assigned_records": 0,
        "candidate_branches": 0,
        "visited_branches": 0,
        "unvisited_branches": 0,
        "canonical_evaluations": 0,
        "improved_branches": 0,
        "rejected_branches": 0,
    }
    prepared_by_index = context.records
    eligible: list[_Candidate] = []
    for index in indices:
        prepared = prepared_by_index[index]
        record = prepared.record
        if not prepared.is_physical:
            counters["nonvisual_records"] += 1
            continue
        if prepared.offset_quality == "unsupported":
            counters["unsupported_model_records"] += 1
            continue
        if record.key in assigned:
            counters["assigned_records"] += 1
            continue
        eligible.append(_Candidate(-1, record, prepared.offset_quality, prepared.uncertainty_m))
    eligible.sort(key=lambda item: item.record.key)
    counters["eligible_records"] = len(eligible)
    digest = hashlib.sha256(
        json.dumps(
            [(item.report_index, item.record.key) for item in eligible],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return eligible, counters, digest


def expand_assignments(
    scorer: CandidateScorer,
    seed: Hypothesis,
    *,
    config: ExpansionConfig,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    result_callback: Callable[[dict[str, Any]], None] | None = None,
    collect_timing: bool = False,
) -> ExpansionResult:
    """Explore one unmatched report plus one unused physical catalog record.

    Branches are a local single-addition heuristic. The canonical scorer decides
    whether water, visibility, merging, types, and complete reassignment make a
    proposal an exact objective improvement.

    When collect_timing is true, stage_timings contains aggregate CPU wall times
    for this mode. Residual time is nested within solver_fit_wall_s, and the
    uninstrumented remainder excludes all non-overlapping recorded stages.
    """
    started = perf_counter()
    if type(collect_timing) is not bool:
        raise TypeError("collect_timing must be bool.")
    stage_timings = _new_stage_timings() if collect_timing else None

    def early_result(
        status: ExpansionStatus,
        *,
        completed: bool,
        budget_exhausted: bool = False,
    ) -> ExpansionResult:
        return _result(
            seed,
            status=status,
            completed=completed,
            budget_exhausted=budget_exhausted,
            stage_timings=stage_timings,
            started=started,
        )

    if not isinstance(scorer, CandidateScorer):
        raise TypeError("scorer must be a CandidateScorer instance.")
    if not isinstance(seed, Hypothesis):
        raise TypeError("seed must be a Hypothesis instance.")
    if not isinstance(config, ExpansionConfig):
        raise TypeError("config must be an ExpansionConfig instance.")
    bounds = scorer.config.bounds_xy
    if bounds is None:
        return early_result("invalid_bounds", completed=False)
    xmin, xmax, ymin, ymax = bounds
    if not (xmin <= seed.east_m <= xmax and ymin <= seed.north_m <= ymax):
        return early_result("invalid_seed", completed=False)
    measurements = scorer.measurements
    if (
        scorer.config.common_bearing_bias_prior_deg is not None
        or measurements.calibrated_common_bias_mean_deg not in (None, 0.0)
        or measurements.calibrated_common_bias_sigma_deg not in (None, 0.0)
    ):
        return early_result("unsupported_calibration", completed=False)
    if measurements.covariance_deg2 is not None:
        return early_result("unsupported_covariance", completed=False)
    report_count = len(measurements.measurements)
    assigned_reports = [item.report_index for item in seed.assignments]
    if any(index < 0 or index >= report_count for index in assigned_reports):
        return early_result("invalid_assignment", completed=False)
    if len(set(assigned_reports)) != len(assigned_reports):
        return early_result("invalid_assignment", completed=False)
    if len(seed.assignments) < 2:
        return early_result("insufficient_assignments", completed=True)
    seed_validation_started = perf_counter() if stage_timings is not None else None
    try:
        validation_status, seed_evaluation = _seed_validation(scorer, seed)
    finally:
        if seed_validation_started is not None:
            _add_timing(
                stage_timings,
                "seed_validation_wall_s",
                perf_counter() - seed_validation_started,
            )
    if validation_status is not None or seed_evaluation is None:
        return early_result(validation_status or "stale_hypothesis", completed=False)
    report_count = len(measurements.measurements)
    assigned_reports = {item.report_index for item in seed.assignments}
    unmatched = [index for index in range(report_count) if index not in assigned_reports]
    if not unmatched:
        return early_result("no_unmatched_reports", completed=True)
    if not seed.assignments:
        return early_result("insufficient_assignments", completed=True)
    preparation_started = perf_counter() if stage_timings is not None else None
    try:
        seed_context = _prepare_seed(scorer, seed_evaluation, seed)
    except ValueError:
        if preparation_started is not None:
            _add_timing(
                stage_timings,
                "pool_preparation_wall_s",
                perf_counter() - preparation_started,
            )
        return early_result("invalid_assignment", completed=False)
    try:
        eligible, counters, pool_digest = _candidate_pool(scorer, seed, config)
        counters["pool_digest"] = pool_digest
        branches = [
            (report_index, candidate.record.key, candidate)
            for report_index in unmatched
            for candidate in eligible
        ]
        branches.sort(key=lambda item: (item[0], item[1]))
        counters["candidate_branches"] = len(branches)
        counters["unvisited_branches"] = len(branches)
    finally:
        if preparation_started is not None:
            _add_timing(
                stage_timings,
                "pool_preparation_wall_s",
                perf_counter() - preparation_started,
            )
    events: list[dict[str, Any]] = []
    best = seed
    for branch_id, (report_index, _key, candidate_base) in enumerate(branches):
        if counters["visited_branches"] >= config.max_branches:
            counters["unvisited_branches"] = len(branches) - counters["visited_branches"]
            elapsed, finalized_timings = _finalize_stage_timings(stage_timings, started)
            return ExpansionResult(
                seed,
                best,
                "budget_exhausted",
                False,
                True,
                counters,
                tuple(events),
                elapsed,
                finalized_timings,
            )
        candidate = _Candidate(
            report_index,
            candidate_base.record,
            candidate_base.offset_quality,
            candidate_base.uncertainty_m,
        )
        event: dict[str, Any] = {
            "branch_id": branch_id,
            "report_index": report_index,
            "catalog_key": candidate.record.key,
            "status": "started",
            "proposed_pose": None,
            "canonical_status": None,
            "exact_score": None,
            "added_assignment_retained": False,
            "solver_status": None,
            "nfev": 0,
        }
        _timed_callback(
            progress_callback,
            event,
            stage_timings,
            "progress_callback_wall_s",
        )
        counters["visited_branches"] += 1
        counters["unvisited_branches"] = len(branches) - counters["visited_branches"]
        status, proposal, solver_status, nfev, exact, evaluation = _fit_branch(
            scorer,
            seed,
            seed_context,
            candidate,
            config,
            stage_timings=stage_timings,
        )
        event["status"] = status
        event["proposed_pose"] = proposal
        event["solver_status"] = solver_status
        event["nfev"] = nfev
        if evaluation is not None:
            event["canonical_status"] = evaluation.status
            counters["canonical_evaluations"] += 1
        if exact is None:
            counters["rejected_branches"] += 1
            events.append(event)
            _timed_callback(
                result_callback,
                event,
                stage_timings,
                "result_callback_wall_s",
            )
            continue
        event["exact_score"] = exact.score
        event["added_assignment_retained"] = any(
            item.report_index == report_index and item.catalog_key == candidate.record.key
            for item in exact.assignments
        )
        if exact.score < best.score:
            best = exact
            event["status"] = "improved"
            counters["improved_branches"] += 1
        else:
            event["status"] = "no_improvement"
            counters["rejected_branches"] += 1
        events.append(event)
        _timed_callback(
            result_callback,
            event,
            stage_timings,
            "result_callback_wall_s",
        )
    counters["unvisited_branches"] = 0
    elapsed, finalized_timings = _finalize_stage_timings(stage_timings, started)
    return ExpansionResult(
        seed,
        best,
        "completed",
        True,
        False,
        counters,
        tuple(events),
        elapsed,
        finalized_timings,
    )


propose_assignment_expansion = expand_assignments
assignment_expansion = expand_assignments

__all__ = [
    "ExpansionConfig",
    "ExpansionResult",
    "assignment_expansion",
    "expand_assignments",
    "propose_assignment_expansion",
]
