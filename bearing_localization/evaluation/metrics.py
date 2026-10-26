"""Legacy and release-gate evaluation metrics.

The release evaluator consumes truth and pre-search support manifests only
inside the evaluator. Search code must not import this module.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from math import hypot, isfinite, sqrt, ulp
from random import Random
from typing import Literal

from pyproj import CRS, Geod, Transformer
from pyproj.exceptions import ProjError

from maritime_localization_shared.contracts.estimate import PositionEstimate
from maritime_localization_shared.contracts.localization import LocalizationResult
from maritime_localization_shared.contracts.truth import EvaluationTruth


@dataclass(frozen=True, slots=True)
class EvaluationMetrics:
    """Position, AIS-association, and legacy benchmark metrics for one trial."""

    position_error_m: float
    correct_associations: int
    false_associations: int
    missed_associations: int
    association_precision: float
    association_recall: float
    association_f1: float
    success: bool
    success_position_threshold_m: float
    success_min_correct_associations: int


def evaluate_estimate(
    estimate: PositionEstimate,
    truth: EvaluationTruth,
    *,
    success_position_threshold_m: float = 250.0,
    success_min_correct_associations: int = 3,
) -> EvaluationMetrics:
    """Score a v1 estimate against truth using the unchanged 250 m criterion."""
    if not isinstance(estimate, PositionEstimate):
        raise TypeError("estimate must be a PositionEstimate.")
    if not isinstance(truth, EvaluationTruth):
        raise TypeError("truth must be an EvaluationTruth.")
    if not isfinite(success_position_threshold_m) or success_position_threshold_m < 0.0:
        raise ValueError("success_position_threshold_m must be finite and nonnegative.")
    if (
        isinstance(success_min_correct_associations, bool)
        or not isinstance(success_min_correct_associations, int)
        or success_min_correct_associations < 0
    ):
        raise ValueError("success_min_correct_associations must be a nonnegative integer.")

    position_error_m = hypot(
        estimate.east_m - truth.observer_east_m,
        estimate.north_m - truth.observer_north_m,
    )
    true_pairs = {
        (item.observation_index, item.ais_record_id)
        for item in truth.objects
        if item.observation_index is not None and item.ais_record_id is not None
    }
    predicted_pairs = set(estimate.matched_pairs)
    correct = len(true_pairs & predicted_pairs)
    false = len(predicted_pairs - true_pairs)
    missed = len(true_pairs - predicted_pairs)
    precision = correct / len(predicted_pairs) if predicted_pairs else 0.0
    recall = correct / len(true_pairs) if true_pairs else 0.0
    f1 = 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0
    success = (
        position_error_m <= success_position_threshold_m
        and correct >= success_min_correct_associations
    )
    return EvaluationMetrics(
        position_error_m=position_error_m,
        correct_associations=correct,
        false_associations=false,
        missed_associations=missed,
        association_precision=precision,
        association_recall=recall,
        association_f1=f1,
        success=success,
        success_position_threshold_m=success_position_threshold_m,
        success_min_correct_associations=success_min_correct_associations,
    )


@dataclass(frozen=True, slots=True)
class SupportManifest:
    """Pre-search evidence for whether a trial belongs in release support."""

    valid_reference_water: bool
    detected_physical_catalog_matched_groups: int
    port_id: str | None = None
    placement_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.valid_reference_water) is not bool:
            raise TypeError("valid_reference_water must be bool.")
        if (
            type(self.detected_physical_catalog_matched_groups) is not int
            or self.detected_physical_catalog_matched_groups < 0
        ):
            raise ValueError(
                "detected_physical_catalog_matched_groups must be a nonnegative integer."
            )
        for name, value in (("port_id", self.port_id), ("placement_id", self.placement_id)):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be a nonempty string or None.")

    @property
    def supported(self) -> bool:
        """Whether valid reference water and at least three groups were present."""
        return self.valid_reference_water and self.detected_physical_catalog_matched_groups >= 3


AcceptanceStatus = Literal[
    "accepted",
    "ambiguous",
    "no_fix",
    "budget_exhausted",
    "failed",
    "missing",
    "timeout",
    "crash",
    "legacy_unknown",
]
OutcomeStatus = Literal["missing", "timeout", "crash"]


@dataclass(frozen=True, slots=True)
class LocalizationTrialMetrics:
    """One row in the release evaluation, including failed and unsupported trials."""

    manifest: SupportManifest
    acceptance_status: AcceptanceStatus
    position_error_m: float | None
    within_100m: bool | None
    correct_associations: int | None
    false_associations: int | None
    missed_associations: int | None
    association_precision: float | None
    association_recall: float | None
    association_f1: float | None
    elapsed_s: float | None
    failure_category: str | None

    @property
    def accepted(self) -> bool:
        return self.acceptance_status == "accepted"


@dataclass(frozen=True, slots=True)
class ReleaseMetrics:
    """Point rates, confidence intervals, and failure counts for all trial rows."""

    trial_count: int
    supported_trial_count: int
    accepted_count: int
    within_100m_count: int
    availability: float
    conditional_accuracy: float | None
    availability_wilson_95: tuple[float, float]
    conditional_accuracy_wilson_95: tuple[float, float] | None
    accepted_error_percentiles_m: tuple[tuple[int, float], ...] | None
    association_precision: float | None
    association_recall: float | None
    association_f1: float | None
    failure_categories: tuple[tuple[str, int], ...]
    release_pass: bool


def evaluate_localization(
    result: LocalizationResult | PositionEstimate | None,
    truth: EvaluationTruth | None,
    manifest: SupportManifest,
    *,
    outcome_status: OutcomeStatus | None = None,
    coordinate_crs: str | int | CRS | None = None,
) -> LocalizationTrialMetrics:
    """Evaluate one v2 result or v1 estimate against a result-independent manifest.

    V1 estimates have no acceptance decision, so they retain legacy position
    and association diagnostics while their release acceptance remains unknown.
    Pass ``outcome_status`` for absent results caused by missing output, timeout,
    or process crash. When supplied, coordinate_crs declares the common
    CRS of estimate and truth east/north values; error is measured as a WGS84
    ellipsoidal geodesic after transforming both positions to longitude and
    latitude. With None, projected-meter Euclidean distance is preserved.
    """
    source_crs = _parse_coordinate_crs(coordinate_crs)
    if not isinstance(manifest, SupportManifest):
        raise TypeError("manifest must be a SupportManifest.")
    if outcome_status not in (None, "missing", "timeout", "crash"):
        raise ValueError("outcome_status must be missing, timeout, crash, or None.")
    if result is None:
        status: AcceptanceStatus = outcome_status or "missing"
        return _trial_metrics(
            manifest, status, None, None, None, None, None, None, None, None, None, status
        )
    if outcome_status is not None:
        raise ValueError("outcome_status cannot be supplied when a result is present.")
    if truth is not None and not isinstance(truth, EvaluationTruth):
        raise TypeError("truth must be an EvaluationTruth or None.")

    if isinstance(result, PositionEstimate):
        if truth is None:
            return _trial_metrics(
                manifest,
                "legacy_unknown",
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                result.elapsed_s,
                "legacy_unknown_acceptance",
            )
        legacy = evaluate_estimate(result, truth)
        position_error = _position_error_m(
            result.east_m,
            result.north_m,
            truth.observer_east_m,
            truth.observer_north_m,
            source_crs,
        )
        return _trial_metrics(
            manifest,
            "legacy_unknown",
            position_error,
            None,
            legacy.correct_associations,
            legacy.false_associations,
            legacy.missed_associations,
            legacy.association_precision,
            legacy.association_recall,
            legacy.association_f1,
            result.elapsed_s,
            "legacy_unknown_acceptance",
        )
    if not isinstance(result, LocalizationResult):
        raise TypeError("result must be a LocalizationResult, PositionEstimate, or None.")

    if result.search_status == "budget_exhausted":
        status = "budget_exhausted"
    elif result.search_status == "failed":
        status = "failed"
    else:
        status = result.decision_status
    if status != "accepted":
        return _trial_metrics(
            manifest,
            status,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            result.elapsed_s,
            status,
        )
    if truth is None:
        return _trial_metrics(
            manifest,
            "accepted",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            result.elapsed_s,
            "missing_truth",
        )
    assert result.accepted_index is not None
    hypothesis = result.hypotheses[result.accepted_index]
    position_error = _position_error_m(
        hypothesis.east_m,
        hypothesis.north_m,
        truth.observer_east_m,
        truth.observer_north_m,
        source_crs,
    )
    true_by_group = {
        (item.observation_index, key)
        for item in truth.objects
        if item.observation_index is not None
        for key in (item.ais_record_id, item.object_id)
        if key is not None
    }
    predicted = {(item.report_index, item.catalog_key) for item in hypothesis.assignments}
    correct = len(true_by_group & predicted)
    false = len(predicted - true_by_group)
    missed = len(true_by_group - predicted)
    precision = correct / len(predicted) if predicted else 0.0
    recall = correct / len(true_by_group) if true_by_group else 0.0
    f1 = 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0
    return _trial_metrics(
        manifest,
        "accepted",
        position_error,
        _within_100m(position_error),
        correct,
        false,
        missed,
        precision,
        recall,
        f1,
        result.elapsed_s,
        None,
    )


def _within_100m(position_error_m: float) -> bool:
    return position_error_m <= 100.0 or position_error_m - 100.0 <= 8 * ulp(100.0)


def _parse_coordinate_crs(coordinate_crs: str | int | CRS | None) -> CRS | None:
    if coordinate_crs is None:
        return None
    if isinstance(coordinate_crs, bool):
        raise ValueError("coordinate_crs must identify a projected or geographic CRS.")
    try:
        crs = CRS.from_user_input(coordinate_crs)
    except (ProjError, TypeError, ValueError) as exc:
        raise ValueError(
            "coordinate_crs must identify a valid projected or geographic CRS."
        ) from exc
    if not (crs.is_projected or crs.is_geographic):
        raise ValueError("coordinate_crs must be projected or geographic.")
    return crs


def _position_error_m(
    estimate_east: float,
    estimate_north: float,
    truth_east: float,
    truth_north: float,
    coordinate_crs: CRS | None,
) -> float:
    if coordinate_crs is None:
        return hypot(estimate_east - truth_east, estimate_north - truth_north)
    if coordinate_crs.is_geographic and any(
        abs(latitude) > 90.0 for latitude in (estimate_north, truth_north)
    ):
        raise ValueError("Geographic coordinate_crs requires latitude values in [-90, 90].")
    try:
        to_wgs84 = Transformer.from_crs(
            coordinate_crs,
            CRS.from_epsg(4326),
            always_xy=True,
            allow_ballpark=False,
            only_best=True,
        )
        estimate_lon, estimate_lat = to_wgs84.transform(estimate_east, estimate_north)
        truth_lon, truth_lat = to_wgs84.transform(truth_east, truth_north)
    except (ProjError, TypeError, ValueError) as exc:
        raise ValueError("Could not transform coordinates from coordinate_crs to WGS84.") from exc
    transformed = (estimate_lon, estimate_lat, truth_lon, truth_lat)
    if not all(isfinite(value) for value in transformed):
        raise ValueError("coordinate_crs produced non-finite WGS84 coordinates.")
    if any(abs(latitude) > 90.0 for latitude in (estimate_lat, truth_lat)):
        raise ValueError("coordinate_crs transform produced invalid WGS84 latitude.")
    _, _, distance_m = Geod(ellps="WGS84").inv(truth_lon, truth_lat, estimate_lon, estimate_lat)
    if not isfinite(distance_m) or distance_m < 0.0:
        raise ValueError("coordinate_crs produced an invalid WGS84 geodesic distance.")
    return float(distance_m)


def _trial_metrics(
    manifest: SupportManifest,
    status: AcceptanceStatus,
    error: float | None,
    within: bool | None,
    correct: int | None,
    false: int | None,
    missed: int | None,
    precision: float | None,
    recall: float | None,
    f1: float | None,
    elapsed: float | None,
    failure: str | None,
) -> LocalizationTrialMetrics:
    return LocalizationTrialMetrics(
        manifest,
        status,
        error,
        within,
        correct,
        false,
        missed,
        precision,
        recall,
        f1,
        elapsed,
        failure,
    )


def aggregate_release_metrics(
    trials: Sequence[LocalizationTrialMetrics],
) -> ReleaseMetrics:
    """Aggregate release point gates over all supported trials and all failures.

    Missing or failed runs must be represented as explicit trial rows so they
    remain in the supported denominator.
    """
    if any(not isinstance(trial, LocalizationTrialMetrics) for trial in trials):
        raise TypeError("trials must contain LocalizationTrialMetrics records.")
    observed_supported = sum(trial.manifest.supported for trial in trials)
    denominator = observed_supported
    accepted_rows = [trial for trial in trials if trial.manifest.supported and trial.accepted]
    accepted_count = len(accepted_rows)
    within_count = sum(trial.within_100m is True for trial in accepted_rows)
    availability = accepted_count / denominator if denominator else 0.0
    accuracy = within_count / accepted_count if accepted_count else None
    availability_ci = _wilson_interval(accepted_count, denominator)
    accuracy_ci = _wilson_interval(within_count, accepted_count) if accepted_count else None
    errors = sorted(
        trial.position_error_m for trial in accepted_rows if trial.position_error_m is not None
    )
    percentiles = (
        tuple((p, _percentile(errors, p / 100)) for p in (50, 90, 95, 99)) if errors else None
    )
    assoc_rows = [trial for trial in accepted_rows if trial.association_precision is not None]
    correct_total = sum(trial.correct_associations or 0 for trial in assoc_rows)
    predicted_total = sum(
        (trial.correct_associations or 0) + (trial.false_associations or 0) for trial in assoc_rows
    )
    truth_total = sum(
        (trial.correct_associations or 0) + (trial.missed_associations or 0) for trial in assoc_rows
    )
    assoc_precision = correct_total / predicted_total if predicted_total else 0.0
    assoc_recall = correct_total / truth_total if truth_total else 0.0
    assoc_f1 = (
        2 * assoc_precision * assoc_recall / (assoc_precision + assoc_recall)
        if assoc_precision + assoc_recall
        else 0.0
    )
    failures = Counter(
        trial.failure_category for trial in trials if trial.failure_category is not None
    )
    release_pass = (
        denominator > 0
        and accepted_count > 0
        and availability >= 0.90
        and accuracy is not None
        and accuracy >= 0.95
    )
    return ReleaseMetrics(
        trial_count=len(trials),
        supported_trial_count=denominator,
        accepted_count=accepted_count,
        within_100m_count=within_count,
        availability=availability,
        conditional_accuracy=accuracy,
        availability_wilson_95=availability_ci,
        conditional_accuracy_wilson_95=accuracy_ci,
        accepted_error_percentiles_m=percentiles,
        association_precision=assoc_precision if assoc_rows else None,
        association_recall=assoc_recall if assoc_rows else None,
        association_f1=assoc_f1 if assoc_rows else None,
        failure_categories=tuple(sorted(failures.items())),
        release_pass=release_pass,
    )


def clustered_bootstrap_interval(
    trials: Sequence[LocalizationTrialMetrics],
    *,
    metric: Literal["availability", "conditional_accuracy"] = "availability",
    resamples: int = 2000,
    confidence_level: float = 0.95,
    seed: int = 0,
) -> tuple[float, float] | None:
    """Hierarchical percentile interval resampling ports then placements.

    Repeated realizations remain together within each sampled placement. Rows
    missing either cluster key use a unique per-row cluster to avoid accidental
    merging.
    """
    if metric not in ("availability", "conditional_accuracy"):
        raise ValueError("metric must be availability or conditional_accuracy.")
    if type(resamples) is not int or resamples < 1:
        raise ValueError("resamples must be a positive integer.")
    if not 0.0 < confidence_level < 1.0:
        raise ValueError("confidence_level must be strictly between zero and one.")
    clusters: dict[str, dict[str, list[LocalizationTrialMetrics]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for index, trial in enumerate(trials):
        port = trial.manifest.port_id or f"__row_port_{index}"
        placement = trial.manifest.placement_id or f"__row_placement_{index}"
        clusters[port][placement].append(trial)
    ports = list(clusters)
    if not ports:
        return None
    rng = Random(seed)
    values: list[float] = []
    for _ in range(resamples):
        sampled: list[LocalizationTrialMetrics] = []
        for _ in ports:
            port = rng.choice(ports)
            placements = list(clusters[port])
            for _ in placements:
                placement = rng.choice(placements)
                sampled.extend(clusters[port][placement])
        summary = aggregate_release_metrics(sampled)
        value = summary.availability if metric == "availability" else summary.conditional_accuracy
        if value is not None:
            values.append(value)
    if not values:
        return None
    values.sort()
    tail = (1.0 - confidence_level) / 2.0
    return (_percentile(values, tail), _percentile(values, 1.0 - tail))


def _wilson_interval(
    successes: int, total: int, z: float = 1.959963984540054
) -> tuple[float, float]:
    if total == 0:
        return (0.0, 0.0)
    rate = successes / total
    scale = 1.0 + z * z / total
    center = (rate + z * z / (2 * total)) / scale
    radius = z * sqrt(rate * (1 - rate) / total + z * z / (4 * total * total)) / scale
    return max(0.0, center - radius), min(1.0, center + radius)


def _percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return float("nan")
    if len(values) == 1:
        return float(values[0])
    position = min(max(fraction, 0.0), 1.0) * (len(values) - 1)
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    weight = position - lower
    return float(values[lower] * (1 - weight) + values[upper] * weight)


__all__ = [
    "AcceptanceStatus",
    "OutcomeStatus",
    "EvaluationMetrics",
    "LocalizationTrialMetrics",
    "ReleaseMetrics",
    "SupportManifest",
    "aggregate_release_metrics",
    "clustered_bootstrap_interval",
    "evaluate_estimate",
    "evaluate_localization",
]
