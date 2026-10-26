"""Evaluator-only support and bearing-geometry diagnostics."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import atan2, degrees, isfinite, log, pi, radians, sqrt
from typing import Literal, Protocol

import numpy as np
from scipy.stats import chi2

from maritime.evaluation.metrics import SupportManifest
from maritime.schemas.inputs_v2 import MeasurementSetV2
from maritime.schemas.truth import EvaluationTruth, TrueObject


class PostmergeGroup(Protocol):
    detected: bool
    observation_index: int | None
    representative_object_id: str
    representative_catalog_key: str | None
    catalog_member_keys: tuple[str, ...]


class PostmergeDiagnostics(Protocol):
    in_range_physical_count: int
    line_of_sight_visible_physical_count: int
    merged_physical_group_count: int
    detected_physical_group_count: int
    clutter_report_count: int
    groups: tuple[PostmergeGroup, ...]


GeometryStratum = Literal["<=25m", "25-100m", ">100m", "singular"]


@dataclass(frozen=True, slots=True)
class SceneDifficulty:
    """Pre-search support evidence and descriptive scene diagnostics."""

    support_manifest: SupportManifest
    matched_group_count: int
    fisher_information_m_inv2: tuple[tuple[float, float], tuple[float, float]] | None
    ellipse_major_radius_95_m: float | None
    geometry_stratum: GeometryStratum
    angular_spread_deg: float | None
    largest_angular_gap_deg: float | None
    matched_ranges_m: tuple[float, ...]
    range_min_m: float | None
    range_median_m: float | None
    range_max_m: float | None
    range_density_per_km2: float | None
    distinct_type_count: int
    type_entropy_bits: float
    missed_physical_group_count: int | None
    clutter_report_count: int | None
    merged_physical_group_count: int | None
    merge_member_excess_count: int | None
    competing_mode_gap: float | None


def build_support_manifest(
    valid_reference_water: bool,
    diagnostics: PostmergeDiagnostics,
    *,
    port_id: str | None = None,
    placement_id: str | None = None,
) -> SupportManifest:
    """Derive support from valid water and distinct detected postmerge catalog groups."""
    if type(valid_reference_water) is not bool:
        raise TypeError("valid_reference_water must be bool.")
    groups = getattr(diagnostics, "groups", None)
    if not isinstance(groups, tuple):
        raise TypeError("diagnostics.groups must be a tuple of postmerge groups.")
    identities: set[tuple[str, int | str]] = set()
    for group in groups:
        detected = getattr(group, "detected", None)
        obs = getattr(group, "observation_index", None)
        has_catalog_representative = getattr(group, "representative_catalog_key", None) is not None
        if type(detected) is not bool:
            raise TypeError("each group must expose a bool detected field.")
        if not detected or not has_catalog_representative:
            continue
        if obs is not None:
            if type(obs) is not int or obs < 0:
                raise ValueError("observation_index must be nonnegative or None.")
            identity = ("observation", obs)
        else:
            representative = getattr(group, "representative_object_id", None)
            if not isinstance(representative, str) or not representative:
                raise ValueError("detected groups without observation indices need an identity.")
            identity = ("representative", representative)
        identities.add(identity)
    return SupportManifest(
        valid_reference_water, len(identities), port_id=port_id, placement_id=placement_id
    )


def evaluate_scene_difficulty(
    truth: EvaluationTruth,
    measurements: MeasurementSetV2,
    diagnostics: PostmergeDiagnostics | None = None,
    *,
    valid_reference_water: bool,
    port_id: str | None = None,
    placement_id: str | None = None,
    competing_mode_scores: Sequence[float] | None = None,
    default_sigma_deg: float = 0.5,
    sigma_floor_deg: float = 0.05,
) -> SceneDifficulty:
    """Compute known-association Fisher geometry and evaluator-only scene summaries.

    Bearings are clockwise from true north, hence observer east/north Jacobian
    (-delta_north/r^2, delta_east/r^2). Calibrated common bias uses a prior Schur
    complement. Geometry diagnostics cannot change support eligibility.
    """
    if not isinstance(truth, EvaluationTruth):
        raise TypeError("truth must be an EvaluationTruth.")
    if not isinstance(measurements, MeasurementSetV2):
        raise TypeError("measurements must be a MeasurementSetV2.")
    if not isfinite(default_sigma_deg) or default_sigma_deg <= 0:
        raise ValueError("default_sigma_deg must be finite and positive.")
    if not isfinite(sigma_floor_deg) or sigma_floor_deg <= 0:
        raise ValueError("sigma_floor_deg must be finite and positive.")
    manifest = (
        build_support_manifest(
            valid_reference_water, diagnostics, port_id=port_id, placement_id=placement_id
        )
        if diagnostics is not None
        else SupportManifest(valid_reference_water, 0, port_id, placement_id)
    )
    by_observation: dict[int, TrueObject] = {}
    for obj in truth.objects:
        index = obj.observation_index
        if obj.ais_record_id is None or index is None or index >= len(measurements.measurements):
            continue
        old = by_observation.get(index)
        if old is None or _range(truth, obj) < _range(truth, old):
            by_observation[index] = obj

    bearings: list[float] = []
    ranges: list[float] = []
    types: list[str] = []
    jacobians: list[np.ndarray] = []
    matched_indices: list[int] = []
    sigmas_rad: list[float] = []
    for index, obj in sorted(by_observation.items()):
        dx = obj.east_m - truth.observer_east_m
        dy = obj.north_m - truth.observer_north_m
        range_m = sqrt(dx * dx + dy * dy)
        if not isfinite(range_m) or range_m <= 0:
            continue
        # Derivative of atan2(east, north), with observer coordinates as variables.
        jacobians.append(np.array([-dy / range_m**2, dx / range_m**2]))
        sigma = measurements.measurements[index].sigma_deg
        sigmas_rad.append(
            radians(max(sigma if sigma is not None else default_sigma_deg, sigma_floor_deg))
        )
        matched_indices.append(index)
        bearings.append(degrees(atan2(dx, dy)) % 360.0)
        ranges.append(range_m)
        types.append(obj.object_type)

    fisher: np.ndarray | None = None
    if jacobians:
        design = np.stack(jacobians)
        if measurements.covariance_deg2 is None:
            precision = np.diag(1.0 / np.square(sigmas_rad))
        else:
            full_covariance = np.asarray(measurements.covariance_deg2, dtype=float)
            selected = np.ix_(matched_indices, matched_indices)
            covariance = full_covariance[selected] * radians(1.0) ** 2
            # Respect the configured information floor without changing correlations.
            floor_variances = np.square(np.asarray(sigmas_rad))
            covariance[np.diag_indices_from(covariance)] = np.maximum(
                np.diag(covariance), floor_variances
            )
            covariance_eigenvalues = np.linalg.eigvalsh(covariance)
            if covariance_eigenvalues[0] > max(covariance_eigenvalues[-1], 1.0) * 1e-12:
                precision = np.linalg.inv(covariance)
            else:
                precision = None

        if precision is not None:
            fisher = design.T @ precision @ design
            bias_sigma = measurements.calibrated_common_bias_sigma_deg
            if bias_sigma is not None and bias_sigma > 0:
                cross = design.T @ precision @ np.ones(len(design))
                bias_information = float(np.ones(len(design)) @ precision @ np.ones(len(design)))
                bias_information += 1.0 / radians(bias_sigma) ** 2
                fisher -= np.outer(cross, cross) / bias_information

    radius = None
    fisher_tuple = None
    if fisher is not None and np.isfinite(fisher).all():
        fisher_tuple = _matrix_tuple(fisher)
        eig = np.linalg.eigvalsh(fisher)
        if len(ranges) >= 2 and eig[0] > max(eig[-1], 1.0) * 1e-12:
            cov = np.linalg.inv(fisher)
            radius = sqrt(float(chi2.ppf(0.95, 2)) * float(np.linalg.eigvalsh(cov)[-1]))
    if radius is None or not isfinite(radius):
        stratum: GeometryStratum = "singular"
        radius = None
    elif radius <= 25:
        stratum = "<=25m"
    elif radius <= 100:
        stratum = "25-100m"
    else:
        stratum = ">100m"

    spread, gap = _angular_metrics(bearings)
    ordered = tuple(sorted(ranges))
    median = _median(ordered)
    density = (
        len(ordered) / (pi * (ordered[-1] / 1000) ** 2) if ordered and ordered[-1] > 0 else None
    )
    counts = {kind: types.count(kind) for kind in set(types)}
    entropy = (
        -sum(count / len(types) * log(count / len(types), 2) for count in counts.values())
        if types
        else 0.0
    )
    missed = clutter = merged = excess = None
    if diagnostics is not None:
        missed = sum(not group.detected for group in diagnostics.groups)
        clutter = diagnostics.clutter_report_count
        merged = diagnostics.merged_physical_group_count
        excess = max(0, diagnostics.line_of_sight_visible_physical_count - merged)
    return SceneDifficulty(
        support_manifest=manifest,
        matched_group_count=len(ordered),
        fisher_information_m_inv2=fisher_tuple,
        ellipse_major_radius_95_m=radius,
        geometry_stratum=stratum,
        angular_spread_deg=spread,
        largest_angular_gap_deg=gap,
        matched_ranges_m=ordered,
        range_min_m=ordered[0] if ordered else None,
        range_median_m=median,
        range_max_m=ordered[-1] if ordered else None,
        range_density_per_km2=density,
        distinct_type_count=len(set(types)),
        type_entropy_bits=entropy,
        missed_physical_group_count=missed,
        clutter_report_count=clutter,
        merged_physical_group_count=merged,
        merge_member_excess_count=excess,
        competing_mode_gap=_mode_gap(competing_mode_scores),
    )


def _range(truth: EvaluationTruth, obj: TrueObject) -> float:
    return sqrt(
        (obj.east_m - truth.observer_east_m) ** 2 + (obj.north_m - truth.observer_north_m) ** 2
    )


def _matrix_tuple(value: np.ndarray) -> tuple[tuple[float, float], tuple[float, float]]:
    return ((float(value[0, 0]), float(value[0, 1])), (float(value[1, 0]), float(value[1, 1])))


def _median(values: tuple[float, ...]) -> float | None:
    if not values:
        return None
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2


def _angular_metrics(values: list[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    if len(values) == 1:
        return 0.0, 360.0
    ordered = sorted(value % 360.0 for value in values)
    gaps = [right - left for left, right in zip(ordered, ordered[1:])]
    gaps.append(ordered[0] + 360 - ordered[-1])
    largest = max(gaps)
    return 360 - largest, largest


def _mode_gap(scores: Sequence[float] | None) -> float | None:
    if scores is None:
        return None
    values = tuple(float(score) for score in scores)
    if any(not isfinite(value) for value in values):
        raise ValueError("competing_mode_scores must contain finite values.")
    if len(values) < 2:
        return None
    ordered = sorted(values)
    return ordered[1] - ordered[0]
