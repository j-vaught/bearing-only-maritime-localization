"""Geodesic reverse-bearing cell proposals with explicit witness evidence.

This module samples finite WGS84 reverse rays, projects only the sampled
geographic points into a caller-selected grid frame, and retains explicit
source witnesses for each generated cell. Sampling is a proposal heuristic.
It does not certify continuous geographic coverage or exclude any cell.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import asin, ceil, degrees, isfinite
from typing import Literal

import numpy as np
from numpy.typing import NDArray
from pyproj import Geod, Transformer

from maritime.geometry.coordinates import LocalFrame

from .wedge import GeographicWedgeCatalogObject, WedgeObservation

_GEOD = Geod(ellps="WGS84")


@dataclass(frozen=True, slots=True)
class GeodesicWedgeProposal:
    """One projected grid cell with report and catalog witness evidence."""

    candidate_id: str
    cell_i: int
    cell_j: int
    east_m: float
    north_m: float
    vote_count: int
    matching_count: int
    witness_keys: tuple[str, ...]
    witness_report_keys: tuple[tuple[int, tuple[str, ...]], ...]
    normalized_angular_residual: float
    evidence_score: float

    @property
    def ranking_key(self) -> tuple[int, int, float, float, int, int]:
        """Return the explicit distinct-witness priority key."""
        return (
            -self.matching_count,
            -self.vote_count,
            self.normalized_angular_residual,
            -self.evidence_score,
            self.cell_i,
            self.cell_j,
        )


@dataclass(frozen=True, slots=True)
class GeodesicWedgeProposalResult:
    """All generated cells plus exact source, sampling, and failure accounting."""

    proposals: tuple[GeodesicWedgeProposal, ...]
    observations: tuple[WedgeObservation, ...]
    catalog_keys: tuple[str, ...]
    candidate_cells_total: int
    source_pairs_total: int
    source_pairs_processed: int
    source_pairs_failed: int
    geodesic_samples_total: int
    geodesic_samples_failed: int
    geodesic_samples_degenerate: int
    generation_status: Literal["completed", "budget_exhausted"]
    budget_reason: Literal["none", "source_pairs", "candidate_cells"]
    frame_origin_lonlat: tuple[float, float]
    frame_crs: str
    cell_size_m: float
    max_range_m: float
    angular_tolerance_deg: float
    range_step_m: float
    angular_sample_gap_deg: float
    angular_sample_count: int
    solver_tolerance_deg: float
    max_solver_residual_deg: float

    @property
    def proposal_ids(self) -> tuple[str, ...]:
        return tuple(item.candidate_id for item in self.proposals)

    @property
    def vote_histogram(self) -> tuple[tuple[int, int], ...]:
        counts: dict[int, int] = {}
        for proposal in self.proposals:
            counts[proposal.vote_count] = counts.get(proposal.vote_count, 0) + 1
        return tuple(sorted(counts.items(), reverse=True))


def _wrap_signed(angle_deg: NDArray[np.float64]) -> NDArray[np.float64]:
    return (angle_deg + 180.0) % 360.0 - 180.0


def _maximum_distinct_matching(
    report_to_keys: Mapping[int, Sequence[str]],
) -> int:
    """Return maximum report-to-distinct-catalog-key matching cardinality."""
    source_to_report: dict[str, int] = {}

    def augment(report_index: int, seen: set[str]) -> bool:
        for source_key in report_to_keys.get(report_index, ()):
            if source_key in seen:
                continue
            seen.add(source_key)
            previous_report = source_to_report.get(source_key)
            if previous_report is None or augment(previous_report, seen):
                source_to_report[source_key] = report_index
                return True
        return False

    return sum(1 for report_index in sorted(report_to_keys) if augment(report_index, set()))


def _cell_residual_and_evidence(
    *,
    source_lon_deg: float,
    source_lat_deg: float,
    cell_lon_deg: float,
    cell_lat_deg: float,
    desired_bearing_deg: float,
    cell_size_m: float,
    angular_tolerance_deg: float,
    sigma_deg: float,
    final_distance_m: float,
) -> tuple[bool, float, float]:
    """Check a cell-center bearing interval and return normalized evidence."""
    inverse_bearing, _, distance_m = _GEOD.inv(
        cell_lon_deg,
        cell_lat_deg,
        source_lon_deg,
        source_lat_deg,
    )
    distance = float(distance_m)
    half_diagonal = cell_size_m * 2.0**0.5 / 2.0
    if not isfinite(distance) or distance > final_distance_m:
        return False, float("inf"), 0.0
    if distance <= half_diagonal:
        return True, 1.0, 0.0
    quantization_deg = degrees(asin(min(1.0, half_diagonal / distance)))
    residual_deg = abs(float(_wrap_signed(np.asarray(inverse_bearing - desired_bearing_deg))))
    threshold_deg = angular_tolerance_deg + quantization_deg
    if residual_deg > threshold_deg:
        return False, float("inf"), 0.0
    normalized = min(1.0, residual_deg / max(sigma_deg + quantization_deg, 1e-12))
    precision = min(1.0, sigma_deg / max(sigma_deg + quantization_deg, 1e-12))
    return True, normalized, precision * (1.0 - normalized)


def _neighbor_cell_indices(
    local_x: NDArray[np.float64],
    local_y: NDArray[np.float64],
    valid: NDArray[np.bool_],
    cell_size_m: float,
) -> NDArray[np.int64]:
    """Return the lexicographically sorted unique nine-cell sample splat."""
    x = local_x.reshape(-1)[valid.reshape(-1)]
    y = local_y.reshape(-1)[valid.reshape(-1)]
    if not x.size:
        return np.empty((0, 2), dtype=np.int64)
    base_i = np.floor(x / cell_size_m).astype(np.int64)
    base_j = np.floor(y / cell_size_m).astype(np.int64)
    offsets = np.asarray((-1, 0, 1), dtype=np.int64)
    neighbors_i = (base_i[:, None] + offsets[None, :])[:, :, None]
    neighbors_j = (base_j[:, None] + offsets[None, :])[:, None, :]
    cells = np.column_stack(
        (
            np.broadcast_to(neighbors_i, (x.size, 3, 3)).reshape(-1),
            np.broadcast_to(neighbors_j, (x.size, 3, 3)).reshape(-1),
        )
    )
    minimum_i = int(cells[:, 0].min())
    maximum_i = int(cells[:, 0].max())
    minimum_j = int(cells[:, 1].min())
    maximum_j = int(cells[:, 1].max())
    width_j = maximum_j - minimum_j + 1
    height_i = maximum_i - minimum_i + 1
    int64_max = np.iinfo(np.int64).max
    if width_j <= 0 or height_i > int64_max // width_j:
        return np.unique(cells, axis=0)
    keys = (cells[:, 0] - minimum_i) * width_j + (cells[:, 1] - minimum_j)
    unique_keys = np.unique(keys)
    return np.column_stack(
        (unique_keys // width_j + minimum_i, unique_keys % width_j + minimum_j)
    ).astype(np.int64, copy=False)


def _cell_residual_and_evidence_batch(
    *,
    source_lon_deg: float,
    source_lat_deg: float,
    cell_lon_deg: NDArray[np.float64],
    cell_lat_deg: NDArray[np.float64],
    desired_bearing_deg: float,
    cell_size_m: float,
    angular_tolerance_deg: float,
    sigma_deg: float,
    final_distance_m: float,
) -> tuple[NDArray[np.bool_], NDArray[np.float64], NDArray[np.float64]]:
    """Evaluate a bounded vector of cell centers, scalar-checking boundaries."""
    bearings, _, distances_raw = _GEOD.inv(
        cell_lon_deg,
        cell_lat_deg,
        np.full(cell_lon_deg.shape, source_lon_deg, dtype=np.float64),
        np.full(cell_lat_deg.shape, source_lat_deg, dtype=np.float64),
    )
    bearings = np.asarray(bearings, dtype=np.float64)
    distances = np.asarray(distances_raw, dtype=np.float64)
    half_diagonal = cell_size_m * 2.0**0.5 / 2.0
    finite = np.isfinite(distances)
    near_range = np.abs(distances - final_distance_m) <= 1e-6
    near_origin = np.abs(distances - half_diagonal) <= 1e-6
    far_enough = finite & (distances > half_diagonal)
    quantization = np.zeros_like(distances)
    quantization[far_enough] = np.degrees(
        np.arcsin(np.minimum(1.0, half_diagonal / distances[far_enough]))
    )
    residual = np.abs(_wrap_signed(bearings - desired_bearing_deg))
    threshold = angular_tolerance_deg + quantization
    near_angle = far_enough & (np.abs(residual - threshold) <= 1e-10)
    fallback = near_range | near_origin | near_angle

    within_range = finite & (distances <= final_distance_m)
    accepted = np.zeros(distances.shape, dtype=bool)
    normalized = np.full(distances.shape, np.inf, dtype=np.float64)
    evidence = np.zeros(distances.shape, dtype=np.float64)
    close = within_range & (distances <= half_diagonal)
    normalized[close] = 1.0
    eligible = (
        finite
        & (distances <= final_distance_m)
        & (distances > half_diagonal)
        & (residual <= threshold)
    )
    accepted = close | eligible
    denominator = np.maximum(sigma_deg + quantization, 1e-12)
    normalized[eligible] = np.minimum(1.0, residual[eligible] / denominator[eligible])
    precision = np.minimum(1.0, sigma_deg / denominator)
    evidence[eligible] = precision[eligible] * (1.0 - normalized[eligible])

    for index in np.flatnonzero(fallback):
        scalar = _cell_residual_and_evidence(
            source_lon_deg=source_lon_deg,
            source_lat_deg=source_lat_deg,
            cell_lon_deg=float(cell_lon_deg[index]),
            cell_lat_deg=float(cell_lat_deg[index]),
            desired_bearing_deg=desired_bearing_deg,
            cell_size_m=cell_size_m,
            angular_tolerance_deg=angular_tolerance_deg,
            sigma_deg=sigma_deg,
            final_distance_m=final_distance_m,
        )
        accepted[index], normalized[index], evidence[index] = scalar
    return accepted, normalized, evidence


def generate_geographic_reverse_wedge_proposals_geodesic(
    observations: Sequence[WedgeObservation],
    catalog: Sequence[GeographicWedgeCatalogObject],
    *,
    frame: LocalFrame,
    max_range_m: float,
    cell_size_m: float = 250.0,
    angular_tolerance_deg: float = 0.5,
    max_source_pairs: int | None = None,
    max_candidate_cells: int = 100_000,
    source_chunk_size: int = 256,
    range_step_m: float | None = None,
    observation_sigmas_deg: Mapping[int, float] | None = None,
    solver_tolerance_deg: float = 1e-7,
) -> GeodesicWedgeProposalResult:
    """Generate cells from sampled WGS84 reverse rays.

    The A04 reverse-bearing solver is called in source and range batches.
    Candidate cells are only indexed in the projected frame; all bearing
    interval checks use geographic inverse bearings at cell centers.
    """
    if not isinstance(frame, LocalFrame):
        raise TypeError("frame must be a LocalFrame.")
    for name, value in (("max_range_m", max_range_m), ("cell_size_m", cell_size_m)):
        if not isfinite(value) or value <= 0.0:
            raise ValueError(f"{name} must be finite and positive.")
    if not isfinite(angular_tolerance_deg) or not 0.0 <= angular_tolerance_deg < 90.0:
        raise ValueError("angular_tolerance_deg must be finite and in [0, 90).")
    if max_source_pairs is not None and (type(max_source_pairs) is not int or max_source_pairs < 1):
        raise ValueError("max_source_pairs must be a positive integer or None.")
    if type(max_candidate_cells) is not int or max_candidate_cells < 1:
        raise ValueError("max_candidate_cells must be a positive integer.")
    if type(source_chunk_size) is not int or source_chunk_size < 1:
        raise ValueError("source_chunk_size must be a positive integer.")
    if range_step_m is not None and (not isfinite(range_step_m) or range_step_m <= 0.0):
        raise ValueError("range_step_m must be finite and positive when supplied.")
    if not isfinite(solver_tolerance_deg) or solver_tolerance_deg <= 0.0:
        raise ValueError("solver_tolerance_deg must be finite and positive.")

    obs = tuple(sorted(observations, key=lambda item: item.report_index))
    objects = tuple(sorted(catalog, key=lambda item: item.key))
    if not obs:
        raise ValueError("At least one bearing observation is required.")
    if any(not isinstance(item, WedgeObservation) for item in obs):
        raise TypeError("observations must contain WedgeObservation records.")
    if any(not isinstance(item, GeographicWedgeCatalogObject) for item in objects):
        raise TypeError("catalog must contain GeographicWedgeCatalogObject records.")
    if len({item.report_index for item in obs}) != len(obs):
        raise ValueError("report_index values must be unique.")
    if len({item.key for item in objects}) != len(objects):
        raise ValueError("catalog keys must be unique.")

    sigma_by_report: dict[int, float] = {}
    if observation_sigmas_deg is not None:
        unknown = set(observation_sigmas_deg) - {item.report_index for item in obs}
        if unknown:
            raise ValueError("observation_sigmas_deg contains an unknown report index.")
    for item in obs:
        sigma = (
            max(angular_tolerance_deg, 1e-9)
            if observation_sigmas_deg is None
            else observation_sigmas_deg.get(item.report_index, max(angular_tolerance_deg, 1e-9))
        )
        if not isfinite(sigma) or sigma <= 0.0:
            raise ValueError("observation sigma values must be finite and positive.")
        sigma_by_report[item.report_index] = float(sigma)

    from maritime.geometry.reverse_bearing import (
        solve_reverse_bearing_observer,
    )

    maximum_solver_tolerance = min(0.001, 0.01 * min(sigma_by_report.values()))
    if solver_tolerance_deg > maximum_solver_tolerance:
        raise ValueError(
            "solver_tolerance_deg must not exceed min(0.001 degrees, "
            "0.01 times the minimum effective observation sigma)."
        )

    source_lonlat = np.asarray(
        [(item.longitude_deg, item.latitude_deg) for item in objects],
        dtype=np.float64,
    ).reshape((-1, 2))
    source_total = len(obs) * len(objects)
    effective_source_limit = source_total if max_source_pairs is None else max_source_pairs

    half_diagonal = cell_size_m * 2.0**0.5 / 2.0
    final_distance_m = max_range_m + half_diagonal
    step_m = min(cell_size_m / 2.0, range_step_m or cell_size_m / 2.0)
    sample_count = max(1, int(ceil(final_distance_m / step_m)) + 1)
    sample_distances = np.arange(sample_count, dtype=np.float64) * step_m
    sample_distances[-1] = final_distance_m
    sample_distances.setflags(write=False)
    angular_gap_rad = 2.0 * asin(
        min(1.0, cell_size_m / (4.0 * max(max_range_m, cell_size_m / 2.0)))
    )
    angular_gap_deg = degrees(angular_gap_rad)
    if angular_tolerance_deg == 0.0:
        angular_offsets_deg = np.asarray([0.0], dtype=np.float64)
    else:
        angular_count = max(1, int(ceil(2.0 * angular_tolerance_deg / angular_gap_deg)))
        angular_offsets_deg = np.linspace(
            -angular_tolerance_deg,
            angular_tolerance_deg,
            angular_count + 1,
            dtype=np.float64,
        )
        if not np.any(np.isclose(angular_offsets_deg, 0.0, atol=1e-12)):
            angular_offsets_deg = np.sort(np.concatenate((angular_offsets_deg, np.asarray([0.0]))))
    angular_offsets_deg.setflags(write=False)

    forward = Transformer.from_crs("EPSG:4326", frame.projected_crs, always_xy=True)
    inverse = Transformer.from_crs(frame.projected_crs, "EPSG:4326", always_xy=True)
    origin_x, origin_y = forward.transform(*frame.origin_lonlat)

    witness_index: dict[tuple[int, int], dict[int, set[str]]] = {}
    residual_index: dict[tuple[int, int], dict[int, tuple[float, float]]] = {}
    processed_pairs = 0
    failed_pairs = 0
    sample_total = 0
    sample_failed = 0
    sample_degenerate = 0
    max_solver_residual = 0.0
    budget_reason: Literal["none", "source_pairs", "candidate_cells"] = "none"

    for observation in obs:
        if budget_reason != "none":
            break
        report_index = observation.report_index
        sigma_deg = sigma_by_report[report_index]
        for chunk_start in range(0, len(objects), source_chunk_size):
            remaining = effective_source_limit - processed_pairs
            if remaining <= 0:
                budget_reason = "source_pairs"
                break
            chunk_size = min(source_chunk_size, remaining, len(objects) - chunk_start)
            source_chunk = source_lonlat[chunk_start : chunk_start + chunk_size]
            desired = (observation.bearing_deg + angular_offsets_deg[None, :, None]) % 360.0
            ranges = sample_distances[None, None, :]
            solved = solve_reverse_bearing_observer(
                source_chunk[:, None, None, :],
                desired,
                ranges,
                tolerance_deg=solver_tolerance_deg,
            )
            points = np.asarray(solved.observer_lonlat_deg, dtype=np.float64)
            converged = np.asarray(solved.converged, dtype=bool)
            degenerate = np.asarray(solved.degenerate, dtype=bool)
            finite_points = np.all(np.isfinite(points), axis=-1)
            valid = finite_points & (converged | degenerate)
            failed_mask = (~converged) & (~degenerate)
            sample_total += int(points.shape[0] * points.shape[1] * points.shape[2])
            sample_failed += int(np.count_nonzero(failed_mask))
            sample_degenerate += int(np.count_nonzero(degenerate))
            max_solver_residual = max(
                max_solver_residual,
                float(np.max(np.abs(solved.angular_residual_deg), initial=0.0)),
            )
            failed_pairs += int(np.count_nonzero(np.any(failed_mask, axis=(1, 2))))

            projected_x, projected_y = forward.transform(points[..., 0], points[..., 1])
            local_x = np.asarray(projected_x, dtype=np.float64) - origin_x
            local_y = np.asarray(projected_y, dtype=np.float64) - origin_y
            chunk_exhausted = False
            for local_row, source_index in enumerate(range(chunk_start, chunk_start + chunk_size)):
                cells = _neighbor_cell_indices(
                    local_x[local_row], local_y[local_row], valid[local_row], cell_size_m
                )
                pair_evidence: dict[tuple[int, int], tuple[float, float]] = {}
                source_lon, source_lat = source_chunk[local_row]
                if cells.size:
                    cell_i_values = cells[:, 0]
                    cell_j_values = cells[:, 1]
                    center_x = (cell_i_values + 0.5) * cell_size_m + origin_x
                    center_y = (cell_j_values + 0.5) * cell_size_m + origin_y
                    center_lon, center_lat = inverse.transform(center_x, center_y)
                    accepted, residuals, evidences = _cell_residual_and_evidence_batch(
                        source_lon_deg=float(source_lon),
                        source_lat_deg=float(source_lat),
                        cell_lon_deg=np.asarray(center_lon, dtype=np.float64),
                        cell_lat_deg=np.asarray(center_lat, dtype=np.float64),
                        desired_bearing_deg=observation.bearing_deg,
                        cell_size_m=cell_size_m,
                        angular_tolerance_deg=angular_tolerance_deg,
                        sigma_deg=sigma_deg,
                        final_distance_m=final_distance_m,
                    )
                    for index in np.flatnonzero(accepted):
                        cell = (int(cell_i_values[index]), int(cell_j_values[index]))
                        pair_evidence[cell] = (float(residuals[index]), float(evidences[index]))
                for cell, evidence in pair_evidence.items():
                    if cell not in witness_index and len(witness_index) >= max_candidate_cells:
                        budget_reason = "candidate_cells"
                        chunk_exhausted = True
                        break
                    report_witnesses = witness_index.setdefault(cell, {})
                    report_witnesses.setdefault(report_index, set()).add(objects[source_index].key)
                    previous = residual_index.setdefault(cell, {}).get(report_index)
                    if previous is None or evidence[0] < previous[0]:
                        residual_index[cell][report_index] = evidence
                processed_pairs += 1
                if chunk_exhausted:
                    break
            if chunk_exhausted:
                break
        if budget_reason != "none":
            break

    if budget_reason == "none" and processed_pairs < source_total:
        budget_reason = "source_pairs"
    proposals: list[GeodesicWedgeProposal] = []
    for (cell_i, cell_j), report_witnesses in witness_index.items():
        report_keys = tuple(
            (report_index, tuple(sorted(source_keys)))
            for report_index, source_keys in sorted(report_witnesses.items())
        )
        source_keys = tuple(sorted({key for keys in report_witnesses.values() for key in keys}))
        matching_count = _maximum_distinct_matching(
            {report_index: keys for report_index, keys in report_keys}
        )
        residuals = residual_index[(cell_i, cell_j)]
        mean_residual = sum(value[0] for value in residuals.values()) / len(residuals)
        evidence_score = sum(value[1] for value in residuals.values())
        proposals.append(
            GeodesicWedgeProposal(
                candidate_id=f"cell:{cell_i}:{cell_j}",
                cell_i=cell_i,
                cell_j=cell_j,
                east_m=(cell_i + 0.5) * cell_size_m,
                north_m=(cell_j + 0.5) * cell_size_m,
                vote_count=len(report_witnesses),
                matching_count=matching_count,
                witness_keys=source_keys,
                witness_report_keys=report_keys,
                normalized_angular_residual=mean_residual,
                evidence_score=evidence_score,
            )
        )
    proposals.sort(key=lambda item: item.ranking_key)
    status: Literal["completed", "budget_exhausted"] = (
        "completed" if budget_reason == "none" else "budget_exhausted"
    )
    return GeodesicWedgeProposalResult(
        proposals=tuple(proposals),
        observations=obs,
        catalog_keys=tuple(item.key for item in objects),
        candidate_cells_total=len(proposals),
        source_pairs_total=source_total,
        source_pairs_processed=processed_pairs,
        source_pairs_failed=failed_pairs,
        geodesic_samples_total=sample_total,
        geodesic_samples_failed=sample_failed,
        geodesic_samples_degenerate=sample_degenerate,
        generation_status=status,
        budget_reason=budget_reason,
        frame_origin_lonlat=frame.origin_lonlat,
        frame_crs=frame.projected_crs.to_string(),
        cell_size_m=float(cell_size_m),
        max_range_m=float(max_range_m),
        angular_tolerance_deg=float(angular_tolerance_deg),
        range_step_m=float(step_m),
        angular_sample_gap_deg=float(angular_gap_deg),
        angular_sample_count=int(angular_offsets_deg.size),
        solver_tolerance_deg=float(solver_tolerance_deg),
        max_solver_residual_deg=float(max_solver_residual),
    )


__all__ = [
    "GeodesicWedgeProposal",
    "GeodesicWedgeProposalResult",
    "generate_geographic_reverse_wedge_proposals_geodesic",
]
