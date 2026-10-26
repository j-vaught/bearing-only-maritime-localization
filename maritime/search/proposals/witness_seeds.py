"""Continuous pairwise seeds from selected geodesic-wedge witness graphs.

Seeds refine only supplied cells. They are proposal geometry and do not score
map visibility, group detections, certify coverage, or authorize acceptance.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from itertools import combinations
from math import isfinite, radians, sin
from typing import Literal

import numpy as np
from pyproj import Geod
from scipy.optimize import least_squares

from maritime.geometry.coordinates import (
    LocalFrame,
    geodesic_bearing_distance,
)

from .geodesic_wedge import GeodesicWedgeProposal
from .pairwise import _geodesic_polar_local_xy, _geodesic_polar_to_geographic
from .wedge import GeographicWedgeCatalogObject, WedgeObservation

_GEOD = Geod(ellps="WGS84")
_BEARING_RESIDUAL_TOLERANCE_DEG = 1e-7
_REJECTION_REASONS = (
    "same_catalog_key",
    "coincident_catalog_positions",
    "near_parallel",
    "negative_range",
    "out_of_range",
    "outside_cell",
    "numerical_failure",
)


@dataclass(frozen=True, slots=True)
class WitnessSeed:
    """One continuous reverse-bearing intersection inside a supplied cell."""

    seed_id: str
    cell_candidate_id: str
    cell_i: int
    cell_j: int
    observation_indices: tuple[int, int]
    catalog_keys: tuple[str, str]
    east_m: float
    north_m: float
    longitude_deg: float
    latitude_deg: float
    ranges_m: tuple[float, float]
    bearing_residuals_deg: tuple[float, float]
    coordinate_model: Literal["wgs84_true_north_geodesic_refined"] = (
        "wgs84_true_north_geodesic_refined"
    )


@dataclass(frozen=True, slots=True)
class WitnessSeedResult:
    """Proposal seeds with complete pair-generation and budget accounting."""

    seeds: tuple[WitnessSeed, ...]
    pair_combinations_total: int
    pairs_considered: int
    pairs_accepted: int
    pairs_rejected: int
    pairs_failed: int
    pairs_remaining: int
    candidate_seeds_generated: int
    generation_status: Literal["completed", "budget_exhausted"]
    budget_reason: Literal["none", "pair_evaluations", "candidate_seeds"]
    rejection_counts: tuple[tuple[str, int], ...]
    range_tolerance_m: float

    @property
    def rejection_counts_by_reason(self) -> dict[str, int]:
        return dict(self.rejection_counts)


def _wrap_signed(angle_deg: float) -> float:
    return (angle_deg + 180.0) % 360.0 - 180.0


def _stable_seed_id(
    cell_id: str,
    reports: tuple[int, int],
    catalog_keys: tuple[str, str],
) -> str:
    payload = json.dumps([cell_id, reports, catalog_keys], ensure_ascii=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def generate_continuous_witness_seeds(
    observations: Sequence[WedgeObservation],
    catalog: Sequence[GeographicWedgeCatalogObject],
    selected_proposals: Sequence[GeodesicWedgeProposal],
    *,
    frame: LocalFrame,
    cell_size_m: float,
    max_range_m: float,
    max_pair_evaluations: int,
    max_candidate_seeds: int,
    cell_tolerance_m: float = 1e-6,
    min_crossing_angle_deg: float = 0.05,
    range_tolerance_m: float = 1e-9,
) -> WitnessSeedResult:
    """Refine selected cells using exact WGS84 true-north bearing directions.

    For each distinct report pair, this consumes only the catalog keys listed
    for those reports in the proposal's witness graph. A local geodesic-polar
    intersection provides the nonlinear solver start; the final candidate is
    refined against WGS84 inverse bearings in the supplied projection frame.
    Returned coordinates are frame-relative projected east/north metres.
    Selected proposal order is preserved for budget priority, while report and
    catalog-key order within each proposal is canonical for stable pair IDs.
    """
    if not isinstance(frame, LocalFrame):
        raise TypeError("frame must be a LocalFrame.")
    for name, value in (("cell_size_m", cell_size_m), ("max_range_m", max_range_m)):
        if not isfinite(value) or value <= 0.0:
            raise ValueError(f"{name} must be finite and positive.")
    if type(max_pair_evaluations) is not int or max_pair_evaluations < 1:
        raise ValueError("max_pair_evaluations must be a positive integer.")
    if type(max_candidate_seeds) is not int or max_candidate_seeds < 1:
        raise ValueError("max_candidate_seeds must be a positive integer.")
    if not isfinite(cell_tolerance_m) or cell_tolerance_m < 0.0:
        raise ValueError("cell_tolerance_m must be finite and nonnegative.")
    if not isfinite(range_tolerance_m) or range_tolerance_m < 0.0:
        raise ValueError("range_tolerance_m must be finite and nonnegative.")
    if not isfinite(min_crossing_angle_deg) or not 0.0 <= min_crossing_angle_deg < 90.0:
        raise ValueError("min_crossing_angle_deg must be finite and in [0, 90).")

    obs = tuple(sorted(observations, key=lambda item: item.report_index))
    objects = tuple(sorted(catalog, key=lambda item: item.key))
    # The caller supplies proposals in ranking order. Keep that priority for
    # bounded runs; sort only each witness graph's internal keys below.
    proposals = tuple(selected_proposals)
    if any(not isinstance(item, WedgeObservation) for item in obs):
        raise TypeError("observations must contain WedgeObservation records.")
    if any(not isinstance(item, GeographicWedgeCatalogObject) for item in objects):
        raise TypeError("catalog must contain GeographicWedgeCatalogObject records.")
    if any(not isinstance(item, GeodesicWedgeProposal) for item in proposals):
        raise TypeError("selected_proposals must contain GeodesicWedgeProposal records.")
    if not obs:
        raise ValueError("At least one bearing observation is required.")
    if len({item.report_index for item in obs}) != len(obs):
        raise ValueError("Observation report indices must be unique.")
    if len({item.key for item in objects}) != len(objects):
        raise ValueError("Catalog keys must be unique.")
    if len({item.candidate_id for item in proposals}) != len(proposals):
        raise ValueError("Selected proposal candidate IDs must be unique.")

    observations_by_index = {item.report_index: item for item in obs}
    objects_by_key = {item.key: item for item in objects}
    prepared_proposals: list[
        tuple[GeodesicWedgeProposal, tuple[tuple[WedgeObservation, tuple[str, ...]], ...]]
    ] = []
    total_pairs = 0
    for proposal in proposals:
        report_keys: dict[int, tuple[str, ...]] = {}
        for report_index, source_keys in proposal.witness_report_keys:
            if report_index in report_keys:
                raise ValueError("A proposal witness graph repeats a report index.")
            if report_index not in observations_by_index:
                raise ValueError("A proposal witness graph references an unknown report index.")
            if any(key not in objects_by_key for key in source_keys):
                raise ValueError("A proposal witness graph references an unknown catalog key.")
            report_keys[report_index] = tuple(sorted(set(source_keys)))
        report_rows = tuple(
            (observations_by_index[report_index], keys)
            for report_index, keys in sorted(report_keys.items())
        )
        total_pairs += sum(
            len(keys_i) * len(keys_j) for (_, keys_i), (_, keys_j) in combinations(report_rows, 2)
        )
        prepared_proposals.append((proposal, report_rows))

    def iter_pair_records():
        for proposal, report_rows in prepared_proposals:
            for (observation_i, keys_i), (observation_j, keys_j) in combinations(report_rows, 2):
                for key_i in keys_i:
                    for key_j in keys_j:
                        yield proposal, observation_i, observation_j, key_i, key_j

    considered = 0
    rejected = {reason: 0 for reason in _REJECTION_REASONS}
    failed = 0
    seeds: list[WitnessSeed] = []
    budget_reason: Literal["none", "pair_evaluations", "candidate_seeds"] = "none"
    minimum_sine = sin(radians(min_crossing_angle_deg))

    pair_records = iter(iter_pair_records())
    while considered < total_pairs:
        if considered >= max_pair_evaluations:
            budget_reason = "pair_evaluations"
            break
        if len(seeds) >= max_candidate_seeds:
            budget_reason = "candidate_seeds"
            break
        try:
            proposal, observation_i, observation_j, key_i, key_j = next(pair_records)
        except StopIteration:
            break
        considered += 1
        if key_i == key_j:
            rejected["same_catalog_key"] += 1
            continue
        ui = np.asarray(
            [
                sin(radians(observation_i.bearing_deg)),
                np.cos(radians(observation_i.bearing_deg)),
            ],
            dtype=np.float64,
        )
        uj = np.asarray(
            [
                sin(radians(observation_j.bearing_deg)),
                np.cos(radians(observation_j.bearing_deg)),
            ],
            dtype=np.float64,
        )
        matrix = np.column_stack((ui, -uj))
        determinant = float(np.linalg.det(matrix))
        if abs(determinant) < minimum_sine:
            rejected["near_parallel"] += 1
            continue

        source_i = objects_by_key[key_i]
        source_j = objects_by_key[key_j]
        pair_azimuth, _, pair_distance = _GEOD.inv(
            source_i.longitude_deg,
            source_i.latitude_deg,
            source_j.longitude_deg,
            source_j.latitude_deg,
        )
        if not isfinite(pair_distance):
            failed += 1
            rejected["numerical_failure"] += 1
            continue
        if pair_distance <= 0.0:
            rejected["coincident_catalog_positions"] += 1
            continue
        midpoint_lon, midpoint_lat, _ = _GEOD.fwd(
            source_i.longitude_deg,
            source_i.latitude_deg,
            pair_azimuth,
            pair_distance / 2.0,
        )
        midpoint = (float(midpoint_lon), float(midpoint_lat))
        p_i = _geodesic_polar_local_xy(
            midpoint, (source_i.longitude_deg, source_i.latitude_deg), _GEOD
        )
        p_j = _geodesic_polar_local_xy(
            midpoint, (source_j.longitude_deg, source_j.latitude_deg), _GEOD
        )
        try:
            range_i0, range_j0 = np.linalg.solve(matrix, np.asarray(p_i) - np.asarray(p_j))
        except np.linalg.LinAlgError:
            rejected["near_parallel"] += 1
            continue
        if range_i0 <= 0.0 or range_j0 <= 0.0:
            rejected["negative_range"] += 1
            continue

        candidate_guess_lonlat = _geodesic_polar_to_geographic(
            midpoint,
            float(p_i[0] - range_i0 * ui[0]),
            float(p_i[1] - range_i0 * ui[1]),
            _GEOD,
        )
        guess_east, guess_north = frame.to_local(candidate_guess_lonlat)
        lower_east = proposal.cell_i * cell_size_m
        upper_east = (proposal.cell_i + 1) * cell_size_m
        lower_north = proposal.cell_j * cell_size_m
        upper_north = (proposal.cell_j + 1) * cell_size_m
        solver_lower = np.asarray(
            [lower_east - cell_tolerance_m, lower_north - cell_tolerance_m],
            dtype=np.float64,
        )
        solver_upper = np.asarray(
            [upper_east + cell_tolerance_m, upper_north + cell_tolerance_m],
            dtype=np.float64,
        )
        start = np.asarray(
            [
                np.clip(guess_east, solver_lower[0], solver_upper[0]),
                np.clip(guess_north, solver_lower[1], solver_upper[1]),
            ],
            dtype=np.float64,
        )

        def residual(candidate_en: np.ndarray) -> np.ndarray:
            lonlat = frame.to_geographic(float(candidate_en[0]), float(candidate_en[1]))
            bearing_i = geodesic_bearing_distance(
                lonlat, (source_i.longitude_deg, source_i.latitude_deg)
            ).true_bearing_deg
            bearing_j = geodesic_bearing_distance(
                lonlat, (source_j.longitude_deg, source_j.latitude_deg)
            ).true_bearing_deg
            return np.asarray(
                [
                    _wrap_signed(bearing_i - observation_i.bearing_deg),
                    _wrap_signed(bearing_j - observation_j.bearing_deg),
                ],
                dtype=np.float64,
            )

        try:
            solution = least_squares(
                residual,
                start,
                bounds=(solver_lower, solver_upper),
                xtol=1e-12,
                ftol=1e-12,
                gtol=1e-12,
                max_nfev=100,
            )
            if not solution.success or not np.isfinite(solution.x).all():
                failed += 1
                rejected["numerical_failure"] += 1
                continue
            if not (
                lower_east - cell_tolerance_m <= solution.x[0] <= upper_east + cell_tolerance_m
                and lower_north - cell_tolerance_m
                <= solution.x[1]
                <= upper_north + cell_tolerance_m
            ):
                rejected["outside_cell"] += 1
                continue
            # Preserve the refined location; the declared numerical cell
            # tolerance admits only a tiny solver overshoot at a cell edge.
            east_m = float(solution.x[0])
            north_m = float(solution.x[1])
            lonlat = frame.to_geographic(east_m, north_m)
            bearing_i = geodesic_bearing_distance(
                lonlat, (source_i.longitude_deg, source_i.latitude_deg)
            ).true_bearing_deg
            bearing_j = geodesic_bearing_distance(
                lonlat, (source_j.longitude_deg, source_j.latitude_deg)
            ).true_bearing_deg
            residual_i = _wrap_signed(bearing_i - observation_i.bearing_deg)
            residual_j = _wrap_signed(bearing_j - observation_j.bearing_deg)
            if max(abs(residual_i), abs(residual_j)) > _BEARING_RESIDUAL_TOLERANCE_DEG:
                rejected["outside_cell"] += 1
                continue
            if not (
                lower_east - cell_tolerance_m <= east_m <= upper_east + cell_tolerance_m
                and lower_north - cell_tolerance_m <= north_m <= upper_north + cell_tolerance_m
            ):
                rejected["outside_cell"] += 1
                continue
            range_i = geodesic_bearing_distance(
                lonlat, (source_i.longitude_deg, source_i.latitude_deg)
            ).distance_m
            range_j = geodesic_bearing_distance(
                lonlat, (source_j.longitude_deg, source_j.latitude_deg)
            ).distance_m
            if range_i <= 0.0 or range_j <= 0.0:
                rejected["negative_range"] += 1
                continue
            if (
                range_i > max_range_m + range_tolerance_m
                or range_j > max_range_m + range_tolerance_m
            ):
                rejected["out_of_range"] += 1
                continue
        except (ArithmeticError, ValueError, FloatingPointError, OverflowError, RuntimeError):
            failed += 1
            rejected["numerical_failure"] += 1
            continue

        seed = WitnessSeed(
            seed_id=_stable_seed_id(
                proposal.candidate_id,
                (observation_i.report_index, observation_j.report_index),
                (key_i, key_j),
            ),
            cell_candidate_id=proposal.candidate_id,
            cell_i=proposal.cell_i,
            cell_j=proposal.cell_j,
            observation_indices=(observation_i.report_index, observation_j.report_index),
            catalog_keys=(key_i, key_j),
            east_m=east_m,
            north_m=north_m,
            longitude_deg=float(lonlat[0]),
            latitude_deg=float(lonlat[1]),
            ranges_m=(range_i, range_j),
            bearing_residuals_deg=(residual_i, residual_j),
        )
        seeds.append(seed)

    remaining = total_pairs - considered
    status: Literal["completed", "budget_exhausted"] = (
        "budget_exhausted" if budget_reason != "none" else "completed"
    )
    failed = rejected["numerical_failure"]
    rejected_total = sum(
        count for reason, count in rejected.items() if reason != "numerical_failure"
    )
    return WitnessSeedResult(
        seeds=tuple(seeds),
        pair_combinations_total=total_pairs,
        pairs_considered=considered,
        pairs_accepted=len(seeds),
        pairs_rejected=rejected_total,
        pairs_failed=failed,
        pairs_remaining=remaining,
        candidate_seeds_generated=len(seeds),
        generation_status=status,
        budget_reason=budget_reason,
        rejection_counts=tuple((reason, rejected[reason]) for reason in _REJECTION_REASONS),
        range_tolerance_m=range_tolerance_m,
    )


__all__ = ["WitnessSeed", "WitnessSeedResult", "generate_continuous_witness_seeds"]
