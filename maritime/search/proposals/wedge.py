"""Finite-range reverse-bearing votes for sparse position proposals.

Votes only prioritize grid cells. They do not score observation/catalog
associations and cannot certify that an excluded cell is impossible.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import asin, atan2, cos, degrees, floor, hypot, isfinite, radians, sin
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from maritime.geometry.coordinates import LocalFrame


@dataclass(frozen=True, slots=True)
class WedgeObservation:
    """One retained true-north bearing and caller-stable report index."""

    report_index: int
    bearing_deg: float

    def __post_init__(self) -> None:
        if (
            type(self.report_index) is not int
            or not 0 <= self.report_index <= np.iinfo(np.int64).max
        ):
            raise ValueError("report_index must be a nonnegative integer.")
        if not isfinite(self.bearing_deg) or not 0.0 <= self.bearing_deg < 360.0:
            raise ValueError("bearing_deg must be finite and in [0, 360).")


@dataclass(frozen=True, slots=True)
class WedgeCatalogObject:
    """A stable catalog key at east/north meter coordinates."""

    key: str
    east_m: float
    north_m: float
    normalized_type: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not self.key.strip():
            raise ValueError("key must be a nonempty stable string.")
        if not isfinite(self.east_m) or not isfinite(self.north_m):
            raise ValueError("Catalog coordinates must be finite.")
        if self.normalized_type is not None and not isinstance(self.normalized_type, str):
            raise ValueError("normalized_type must be a string or None.")


@dataclass(frozen=True, slots=True)
class GeographicWedgeCatalogObject:
    """Geographic catalog object for local-tile adaptation."""

    key: str
    longitude_deg: float
    latitude_deg: float
    normalized_type: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not self.key.strip():
            raise ValueError("key must be a nonempty stable string.")
        if not isfinite(self.longitude_deg) or not -180.0 <= self.longitude_deg <= 180.0:
            raise ValueError("longitude_deg must be finite and in [-180, 180].")
        if not isfinite(self.latitude_deg) or not -90.0 <= self.latitude_deg <= 90.0:
            raise ValueError("latitude_deg must be finite and in [-90, 90].")
        if self.normalized_type is not None and not isinstance(self.normalized_type, str):
            raise ValueError("normalized_type must be a string or None.")


@dataclass(frozen=True, slots=True)
class WedgeProposal:
    """One quantized cell with unique-observation vote support."""

    candidate_id: str
    cell_i: int
    cell_j: int
    east_m: float
    north_m: float
    vote_count: int
    normalized_angular_residual: float = float("inf")
    evidence_score: float = 0.0

    @property
    def ranking_key(self) -> tuple[int, float, float, int, int]:
        """Return an opt-in deterministic priority key for this proposal."""
        return (
            -self.vote_count,
            self.normalized_angular_residual,
            -self.evidence_score,
            self.cell_i,
            self.cell_j,
        )


@dataclass(frozen=True, slots=True)
class WedgeProposalResult:
    """Finite proposals plus the exact inputs and budget accounting for parity."""

    proposals: tuple[WedgeProposal, ...]
    observations: tuple[WedgeObservation, ...]
    catalog_keys: tuple[str, ...]
    catalog_types: tuple[str | None, ...]
    catalog_xy_m: NDArray[np.float64]
    cell_size_m: float
    max_range_m: float
    angular_tolerance_deg: float
    source_pairs_total: int
    source_pairs_processed: int
    candidate_cells_total: int
    generation_status: Literal["completed", "budget_exhausted"]
    budget_reason: Literal["none", "source_pairs", "candidate_cells"]
    coordinate_model: Literal["planar_meter_frame", "local_projected_tile"] = "planar_meter_frame"
    frame_origin_lonlat: tuple[float, float] | None = None
    frame_crs: str | None = None
    catalog_lonlat_deg: NDArray[np.float64] | None = None
    grid_bearing_error_bound_deg: float | None = None

    @property
    def observations_array(self) -> tuple[tuple[int, float], ...]:
        """Return exact report-index and original-bearing rows."""
        return tuple((item.report_index, item.bearing_deg) for item in self.observations)

    @property
    def report_indices_array(self) -> NDArray[np.int64]:
        """Return exact integer report IDs without float conversion."""
        result = np.asarray([item.report_index for item in self.observations], dtype=np.int64)
        result.setflags(write=False)
        return result

    @property
    def bearings_deg_array(self) -> NDArray[np.float64]:
        """Return original true-north bearings in stable report order."""
        result = np.asarray([item.bearing_deg for item in self.observations], dtype=np.float64)
        result.setflags(write=False)
        return result

    @property
    def catalog_keys_array(self) -> NDArray[np.str_]:
        """Return catalog keys as a non-object string array for array consumers."""
        result = np.asarray(self.catalog_keys, dtype=np.str_)
        result.setflags(write=False)
        return result

    @property
    def proposal_ids(self) -> tuple[str, ...]:
        """Return stable candidate IDs in result order."""
        return tuple(proposal.candidate_id for proposal in self.proposals)

    @property
    def proposal_cell_indices_array(self) -> NDArray[np.int64]:
        """Return integer grid indices with shape candidate_count by two."""
        result = np.asarray(
            [(proposal.cell_i, proposal.cell_j) for proposal in self.proposals],
            dtype=np.int64,
        ).reshape((-1, 2))
        result.setflags(write=False)
        return result

    @property
    def proposal_positions_en_m(self) -> NDArray[np.float64]:
        """Return candidate cell centers in the declared local meter frame."""
        result = np.asarray(
            [(proposal.east_m, proposal.north_m) for proposal in self.proposals],
            dtype=np.float64,
        ).reshape((-1, 2))
        result.setflags(write=False)
        return result

    @property
    def vote_counts_array(self) -> NDArray[np.int64]:
        """Return one unique-observation count per proposal."""
        result = np.asarray([proposal.vote_count for proposal in self.proposals], dtype=np.int64)
        result.setflags(write=False)
        return result

    @property
    def vote_histogram(self) -> tuple[tuple[int, int], ...]:
        """Return (vote_count, cell_count) pairs in descending vote order."""
        counts: dict[int, int] = {}
        for proposal in self.proposals:
            counts[proposal.vote_count] = counts.get(proposal.vote_count, 0) + 1
        return tuple(sorted(counts.items(), reverse=True))

    @property
    def catalog_metadata(self) -> dict[str, object]:
        """Return stable catalog keys, type labels, and copied coordinates for consumers."""
        coords = np.array(self.catalog_xy_m, dtype=np.float64, copy=True)
        coords.setflags(write=False)
        metadata: dict[str, object] = {
            "keys": self.catalog_keys,
            "normalized_types": self.catalog_types,
            "coordinates_en_m": coords,
            "coordinate_model": self.coordinate_model,
        }
        if self.frame_origin_lonlat is not None:
            metadata["frame_origin_lonlat"] = self.frame_origin_lonlat
        if self.frame_crs is not None:
            metadata["frame_crs"] = self.frame_crs
        if self.catalog_lonlat_deg is not None:
            lonlat = np.array(self.catalog_lonlat_deg, dtype=np.float64, copy=True)
            lonlat.setflags(write=False)
            metadata["positions_lonlat_deg"] = lonlat
        if self.grid_bearing_error_bound_deg is not None:
            metadata["grid_bearing_error_bound_deg"] = self.grid_bearing_error_bound_deg
        return metadata

    @property
    def complete(self) -> bool:
        """Whether every observation/catalog source pair was processed."""
        return self.generation_status == "completed"


class _CandidateRasterBudgetExceeded(Exception):
    """Internal signal that one source wedge exceeded its bounded raster set."""


def _angle_error_deg(first_deg: float, second_deg: float) -> float:
    return abs((first_deg - second_deg + 180.0) % 360.0 - 180.0)


def _normalized_cell_angular_evidence(
    source_east_m: float,
    source_north_m: float,
    cell_i: int,
    cell_j: int,
    bearing_deg: float,
    *,
    cell_size_m: float,
    angular_tolerance_deg: float,
) -> tuple[float, float]:
    """Return residual and precision-weighted evidence for one accepted claim."""
    half_diagonal = cell_size_m * 2.0**0.5 / 2.0
    center_east = (cell_i + 0.5) * cell_size_m
    center_north = (cell_j + 0.5) * cell_size_m
    delta_east = source_east_m - center_east
    delta_north = source_north_m - center_north
    distance_m = hypot(delta_east, delta_north)
    if distance_m <= half_diagonal:
        # The raster rule intentionally accepts every bearing in this
        # enclosure. It is a valid vote but carries no angular evidence.
        return 1.0, 0.0
    center_bearing = degrees(atan2(delta_east, delta_north)) % 360.0
    quantization_deg = degrees(asin(min(1.0, half_diagonal / distance_m)))
    threshold_deg = angular_tolerance_deg + quantization_deg
    angle_error_deg = _angle_error_deg(center_bearing, bearing_deg)
    if threshold_deg <= 0.0:
        residual = 0.0 if angle_error_deg <= 1e-12 else float("inf")
        return residual, 1.0 if residual == 0.0 else 0.0
    residual = min(1.0, max(0.0, angle_error_deg / threshold_deg))
    precision = min(1.0, angular_tolerance_deg / threshold_deg)
    return residual, precision * (1.0 - residual)


def _candidate_cells_for_source(
    source_east_m: float,
    source_north_m: float,
    bearing_deg: float,
    *,
    cell_size_m: float,
    max_range_m: float,
    angular_tolerance_deg: float,
    max_cells: int,
) -> set[tuple[int, int]]:
    """Rasterize a finite reverse ray and test quantized cell centers."""
    half_diagonal = cell_size_m * 2.0**0.5 / 2.0
    step_m = cell_size_m / 2.0
    if step_m <= 0.0:
        raise ValueError("cell_size_m is too small to rasterize safely.")
    reverse = radians((bearing_deg + 180.0) % 360.0)
    direction_east, direction_north = sin(reverse), cos(reverse)
    samples: set[tuple[int, int]] = set()
    sample_distance_m = 0.0
    final_distance_m = max_range_m + half_diagonal
    sample_count = 0
    max_samples = max(4, max_cells * 4)

    while sample_distance_m <= final_distance_m:
        sample_count += 1
        if sample_count > max_samples:
            raise _CandidateRasterBudgetExceeded
        sample_east = source_east_m + sample_distance_m * direction_east
        sample_north = source_north_m + sample_distance_m * direction_north
        base_i = floor(sample_east / cell_size_m)
        base_j = floor(sample_north / cell_size_m)
        # One-cell halo covers centers nearest to each half-cell ray sample.
        for cell_i in range(base_i - 1, base_i + 2):
            for cell_j in range(base_j - 1, base_j + 2):
                center_east = (cell_i + 0.5) * cell_size_m
                center_north = (cell_j + 0.5) * cell_size_m
                delta_east = source_east_m - center_east
                delta_north = source_north_m - center_north
                distance_m = hypot(delta_east, delta_north)
                if distance_m > final_distance_m:
                    continue
                if distance_m > half_diagonal:
                    center_bearing = degrees(atan2(delta_east, delta_north)) % 360.0
                    quantization_deg = degrees(asin(min(1.0, half_diagonal / distance_m)))
                    if _angle_error_deg(center_bearing, bearing_deg) > (
                        angular_tolerance_deg + quantization_deg
                    ):
                        continue
                samples.add((cell_i, cell_j))
                if len(samples) > max_cells:
                    raise _CandidateRasterBudgetExceeded
        sample_distance_m += step_m
    return samples


def _candidate_cells_for_sources_batch(
    source_east_m: NDArray[np.float64],
    source_north_m: NDArray[np.float64],
    bearing_deg: float,
    *,
    cell_size_m: float,
    max_range_m: float,
    angular_tolerance_deg: float,
    max_cells: int,
) -> tuple[set[tuple[int, int]], ...]:
    """Rasterize a source chunk with scalar fallback at numerical boundaries."""
    east = np.asarray(source_east_m, dtype=np.float64).reshape(-1)
    north = np.asarray(source_north_m, dtype=np.float64).reshape(-1)
    if east.shape != north.shape:
        raise ValueError("source coordinate arrays must have the same shape.")
    if east.size == 0:
        return ()
    half_diagonal = cell_size_m * 2.0**0.5 / 2.0
    step_m = cell_size_m / 2.0
    if step_m <= 0.0:
        raise ValueError("cell_size_m is too small to rasterize safely.")
    reverse = radians((bearing_deg + 180.0) % 360.0)
    direction_east, direction_north = sin(reverse), cos(reverse)
    final_distance_m = max_range_m + half_diagonal
    sample_distances: list[float] = []
    sample_distance_m = 0.0
    max_samples = max(4, max_cells * 4)
    while sample_distance_m <= final_distance_m:
        if len(sample_distances) >= max_samples:
            raise _CandidateRasterBudgetExceeded
        sample_distances.append(sample_distance_m)
        sample_distance_m += step_m
    distances = np.asarray(sample_distances, dtype=np.float64)
    sample_east = east[:, None] + distances[None, :] * direction_east
    sample_north = north[:, None] + distances[None, :] * direction_north
    base_i_float = np.floor(sample_east / cell_size_m)
    base_j_float = np.floor(sample_north / cell_size_m)
    int_info = np.iinfo(np.int64)
    safe_rows = (
        np.all(base_i_float >= int_info.min, axis=1)
        & np.all(base_i_float <= int_info.max, axis=1)
        & np.all(base_j_float >= int_info.min, axis=1)
        & np.all(base_j_float <= int_info.max, axis=1)
    )
    results: list[set[tuple[int, int]]] = [set() for _ in range(east.size)]
    if np.any(~safe_rows):
        for row in np.flatnonzero(~safe_rows):
            results[int(row)] = _candidate_cells_for_source(
                float(east[row]),
                float(north[row]),
                bearing_deg,
                cell_size_m=cell_size_m,
                max_range_m=max_range_m,
                angular_tolerance_deg=angular_tolerance_deg,
                max_cells=max_cells,
            )
    if not np.any(safe_rows):
        return tuple(results)
    safe_indices = np.flatnonzero(safe_rows)
    base_i = base_i_float[safe_rows].astype(np.int64, copy=False)
    base_j = base_j_float[safe_rows].astype(np.int64, copy=False)
    offsets = np.asarray(
        [(cell_i, cell_j) for cell_i in (-1, 0, 1) for cell_j in (-1, 0, 1)],
        dtype=np.int64,
    )
    cell_i = base_i[:, :, None] + offsets[None, None, :, 0]
    cell_j = base_j[:, :, None] + offsets[None, None, :, 1]
    center_east = (cell_i.astype(np.float64) + 0.5) * cell_size_m
    center_north = (cell_j.astype(np.float64) + 0.5) * cell_size_m
    delta_east = east[safe_rows, None, None] - center_east
    delta_north = north[safe_rows, None, None] - center_north
    distance_m = np.hypot(delta_east, delta_north)
    accepted = distance_m <= final_distance_m
    outside_halo = distance_m > half_diagonal
    center_bearing = np.degrees(np.arctan2(delta_east, delta_north)) % 360.0
    quantization_deg = np.zeros_like(distance_m)
    ratio = np.minimum(
        1.0, half_diagonal / distance_m, where=outside_halo, out=np.ones_like(distance_m)
    )
    quantization_deg[outside_halo] = np.degrees(np.arcsin(ratio[outside_halo]))
    angle_error = np.abs((center_bearing - bearing_deg + 180.0) % 360.0 - 180.0)
    threshold = angular_tolerance_deg + quantization_deg
    accepted &= (~outside_halo) | (angle_error <= threshold)
    scale = max(
        1.0,
        abs(float(np.max(east[safe_rows]))),
        abs(float(np.min(east[safe_rows]))),
        abs(float(np.max(north[safe_rows]))),
        abs(float(np.min(north[safe_rows]))),
        final_distance_m,
        cell_size_m,
    )
    distance_epsilon = 64.0 * np.finfo(np.float64).eps * scale
    boundary_rows = np.any(np.abs(distance_m - final_distance_m) <= distance_epsilon, axis=(1, 2))
    boundary_rows |= np.any(outside_halo & (np.abs(angle_error - threshold) <= 1e-12), axis=(1, 2))
    fractional_i = np.abs(base_i_float[safe_rows] - np.rint(base_i_float[safe_rows]))
    fractional_j = np.abs(base_j_float[safe_rows] - np.rint(base_j_float[safe_rows]))
    boundary_rows |= np.any(fractional_i <= 1e-13, axis=1)
    boundary_rows |= np.any(fractional_j <= 1e-13, axis=1)
    for local_row, global_row in enumerate(safe_indices):
        if boundary_rows[local_row]:
            results[int(global_row)] = _candidate_cells_for_source(
                float(east[global_row]),
                float(north[global_row]),
                bearing_deg,
                cell_size_m=cell_size_m,
                max_range_m=max_range_m,
                angular_tolerance_deg=angular_tolerance_deg,
                max_cells=max_cells,
            )
            continue
        selected = accepted[local_row]
        results[int(global_row)] = {
            (int(item_i), int(item_j))
            for item_i, item_j in zip(cell_i[local_row][selected], cell_j[local_row][selected])
        }
        if len(results[int(global_row)]) > max_cells:
            raise _CandidateRasterBudgetExceeded
    return tuple(results)


def generate_reverse_wedge_proposals(
    observations: tuple[WedgeObservation, ...] | list[WedgeObservation],
    catalog: tuple[WedgeCatalogObject, ...] | list[WedgeCatalogObject],
    *,
    max_range_m: float,
    cell_size_m: float = 250.0,
    angular_tolerance_deg: float = 0.5,
    max_source_pairs: int = 100_000,
    max_candidate_cells: int = 100_000,
) -> WedgeProposalResult:
    """Generate deterministic sparse reverse-bearing cell votes.

    Each observation contributes at most one vote to any cell, regardless of
    how many catalog objects claim it. Inputs are ordered by stable report
    index and key before traversal, so reordered input batches with the same
    records yield identical cell IDs and vote counts. Python integer vote
    counts avoid fixed-width accumulator overflow. A budget limit is returned
    explicitly and means the proposal set is incomplete.

    Coordinates use a caller-consistent local planar meter frame. For broad
    geographic domains, call this on local tiles whose frame distortion is
    qualified; use geodesic true-north bearings for exact downstream scoring.
    """
    for name, value in (("max_range_m", max_range_m), ("cell_size_m", cell_size_m)):
        if not isfinite(value) or value <= 0.0:
            raise ValueError(f"{name} must be finite and positive.")
    if not isfinite(angular_tolerance_deg) or not 0.0 <= angular_tolerance_deg < 90.0:
        raise ValueError("angular_tolerance_deg must be finite and in [0, 90).")
    if type(max_source_pairs) is not int or max_source_pairs < 1:
        raise ValueError("max_source_pairs must be a positive integer.")
    if type(max_candidate_cells) is not int or max_candidate_cells < 1:
        raise ValueError("max_candidate_cells must be a positive integer.")

    obs = tuple(sorted(observations, key=lambda item: item.report_index))
    objects = tuple(sorted(catalog, key=lambda item: item.key))
    if any(not isinstance(item, WedgeObservation) for item in obs):
        raise TypeError("observations must contain WedgeObservation records.")
    if any(not isinstance(item, WedgeCatalogObject) for item in objects):
        raise TypeError("catalog must contain WedgeCatalogObject records.")
    if len({item.report_index for item in obs}) != len(obs):
        raise ValueError("report_index values must be unique.")
    if len({item.key for item in objects}) != len(objects):
        raise ValueError("catalog keys must be unique.")
    if not obs:
        raise ValueError("At least one bearing observation is required.")

    catalog_xy = np.asarray([(item.east_m, item.north_m) for item in objects], dtype=np.float64)
    catalog_xy = catalog_xy.reshape((-1, 2))
    catalog_xy.setflags(write=False)
    source_total = len(obs) * len(objects)
    # Keep one observation-local set, so vote storage is bounded by two
    # candidate-cell sets rather than cells multiplied by reports.
    votes: dict[tuple[int, int], int] = {}
    processed_pairs = 0
    budget_reason: Literal["none", "source_pairs", "candidate_cells"] = "none"

    for observation in obs:
        observation_cells: set[tuple[int, int]] = set()
        for source in objects:
            if processed_pairs >= max_source_pairs:
                budget_reason = "source_pairs"
                break
            try:
                source_cells = _candidate_cells_for_source(
                    source.east_m,
                    source.north_m,
                    observation.bearing_deg,
                    cell_size_m=cell_size_m,
                    max_range_m=max_range_m,
                    angular_tolerance_deg=angular_tolerance_deg,
                    max_cells=max(1_024, max_candidate_cells),
                )
            except _CandidateRasterBudgetExceeded:
                budget_reason = "candidate_cells"
                break
            for cell in sorted(source_cells):
                if cell in observation_cells:
                    continue
                if cell not in votes and len(votes) >= max_candidate_cells:
                    budget_reason = "candidate_cells"
                    break
                observation_cells.add(cell)
                votes.setdefault(cell, 0)
            if budget_reason == "candidate_cells":
                break
            processed_pairs += 1
        for cell in observation_cells:
            votes[cell] += 1
        if budget_reason != "none":
            break

    # A source-pair cap is exhausted only when unprocessed pairs remain.
    if budget_reason == "none" and processed_pairs < source_total:
        budget_reason = "source_pairs"
    proposals: list[WedgeProposal] = []
    for (cell_i, cell_j), vote_count in votes.items():
        proposals.append(
            WedgeProposal(
                candidate_id=f"cell:{cell_i}:{cell_j}",
                cell_i=cell_i,
                cell_j=cell_j,
                east_m=(cell_i + 0.5) * cell_size_m,
                north_m=(cell_j + 0.5) * cell_size_m,
                vote_count=vote_count,
            )
        )
    proposals.sort(key=lambda item: (-item.vote_count, item.cell_i, item.cell_j))
    status: Literal["completed", "budget_exhausted"] = (
        "completed" if budget_reason == "none" else "budget_exhausted"
    )
    return WedgeProposalResult(
        proposals=tuple(proposals),
        observations=obs,
        catalog_keys=tuple(item.key for item in objects),
        catalog_types=tuple(item.normalized_type for item in objects),
        catalog_xy_m=catalog_xy,
        cell_size_m=float(cell_size_m),
        max_range_m=float(max_range_m),
        angular_tolerance_deg=float(angular_tolerance_deg),
        source_pairs_total=source_total,
        source_pairs_processed=processed_pairs,
        candidate_cells_total=len(votes),
        generation_status=status,
        budget_reason=budget_reason,
    )


def generate_reverse_wedge_proposals_complete(
    observations: tuple[WedgeObservation, ...] | list[WedgeObservation],
    catalog: tuple[WedgeCatalogObject, ...] | list[WedgeCatalogObject],
    *,
    max_range_m: float,
    cell_size_m: float = 250.0,
    angular_tolerance_deg: float = 0.5,
    max_source_pairs: int = 100_000,
    max_candidate_cells: int = 100_000,
    source_chunk_size: int = 4_096,
    collect_metadata: bool = False,
) -> WedgeProposalResult:
    """Accumulate all permitted source-pair votes before deterministic ranking.

    This explicit mode preserves the legacy function's budget semantics while
    batching source geometry. max_candidate_cells remains a hard
    accumulator budget; it is never a final proposal top-K.
    """
    for name, value in (("max_range_m", max_range_m), ("cell_size_m", cell_size_m)):
        if not isfinite(value) or value <= 0.0:
            raise ValueError(f"{name} must be finite and positive.")
    if not isfinite(angular_tolerance_deg) or not 0.0 <= angular_tolerance_deg < 90.0:
        raise ValueError("angular_tolerance_deg must be finite and in [0, 90).")
    if type(max_source_pairs) is not int or max_source_pairs < 1:
        raise ValueError("max_source_pairs must be a positive integer.")
    if type(max_candidate_cells) is not int or max_candidate_cells < 1:
        raise ValueError("max_candidate_cells must be a positive integer.")
    if type(source_chunk_size) is not int or source_chunk_size < 1:
        raise ValueError("source_chunk_size must be a positive integer.")
    if type(collect_metadata) is not bool:
        raise TypeError("collect_metadata must be a boolean.")

    obs = tuple(sorted(observations, key=lambda item: item.report_index))
    objects = tuple(sorted(catalog, key=lambda item: item.key))
    if any(not isinstance(item, WedgeObservation) for item in obs):
        raise TypeError("observations must contain WedgeObservation records.")
    if any(not isinstance(item, WedgeCatalogObject) for item in objects):
        raise TypeError("catalog must contain WedgeCatalogObject records.")
    if len({item.report_index for item in obs}) != len(obs):
        raise ValueError("report_index values must be unique.")
    if len({item.key for item in objects}) != len(objects):
        raise ValueError("catalog keys must be unique.")
    if not obs:
        raise ValueError("At least one bearing observation is required.")

    catalog_xy = np.asarray([(item.east_m, item.north_m) for item in objects], dtype=np.float64)
    catalog_xy = catalog_xy.reshape((-1, 2))
    catalog_xy.setflags(write=False)
    source_total = len(obs) * len(objects)
    votes: dict[tuple[int, int], int] = {}
    residual_sums: dict[tuple[int, int], float] = {}
    evidence_sums: dict[tuple[int, int], float] = {}
    processed_pairs = 0
    budget_reason: Literal["none", "source_pairs", "candidate_cells"] = "none"
    max_source_cells = max(1_024, max_candidate_cells)

    for observation in obs:
        observation_cells: set[tuple[int, int]] = set()
        observation_residuals: dict[tuple[int, int], tuple[float, float]] = {}
        chunk_start = 0
        while chunk_start < len(objects):
            remaining_pairs = max_source_pairs - processed_pairs
            if remaining_pairs <= 0:
                budget_reason = "source_pairs"
                break
            chunk = objects[chunk_start : chunk_start + min(source_chunk_size, remaining_pairs)]
            try:
                source_cells_batch = _candidate_cells_for_sources_batch(
                    np.asarray([item.east_m for item in chunk], dtype=np.float64),
                    np.asarray([item.north_m for item in chunk], dtype=np.float64),
                    observation.bearing_deg,
                    cell_size_m=cell_size_m,
                    max_range_m=max_range_m,
                    angular_tolerance_deg=angular_tolerance_deg,
                    max_cells=max_source_cells,
                )
            except _CandidateRasterBudgetExceeded:
                budget_reason = "candidate_cells"
                break
            for source, source_cells in zip(chunk, source_cells_batch):
                for cell in sorted(source_cells):
                    if collect_metadata:
                        residual, evidence = _normalized_cell_angular_evidence(
                            source.east_m,
                            source.north_m,
                            cell[0],
                            cell[1],
                            observation.bearing_deg,
                            cell_size_m=cell_size_m,
                            angular_tolerance_deg=angular_tolerance_deg,
                        )
                        previous_residual = observation_residuals.get(cell)
                        if previous_residual is not None:
                            if residual < previous_residual[0]:
                                observation_residuals[cell] = (residual, evidence)
                            continue
                    elif cell in observation_cells:
                        continue
                    if cell not in votes and len(votes) >= max_candidate_cells:
                        budget_reason = "candidate_cells"
                        break
                    observation_cells.add(cell)
                    if collect_metadata:
                        observation_residuals[cell] = (residual, evidence)
                    votes.setdefault(cell, 0)
                if budget_reason == "candidate_cells":
                    break
                processed_pairs += 1
            if budget_reason != "none":
                break
            chunk_start += len(chunk)
        for cell in observation_cells:
            votes[cell] += 1
            if collect_metadata:
                residual, evidence = observation_residuals[cell]
                residual_sums[cell] = residual_sums.get(cell, 0.0) + residual
                evidence_sums[cell] = evidence_sums.get(cell, 0.0) + evidence
        if budget_reason != "none":
            break

    if budget_reason == "none" and processed_pairs < source_total:
        budget_reason = "source_pairs"
    proposals = [
        WedgeProposal(
            candidate_id=f"cell:{cell_i}:{cell_j}",
            cell_i=cell_i,
            cell_j=cell_j,
            east_m=(cell_i + 0.5) * cell_size_m,
            north_m=(cell_j + 0.5) * cell_size_m,
            vote_count=vote_count,
            normalized_angular_residual=(
                residual_sums[(cell_i, cell_j)] / vote_count if collect_metadata else float("inf")
            ),
            evidence_score=evidence_sums[(cell_i, cell_j)] if collect_metadata else 0.0,
        )
        for (cell_i, cell_j), vote_count in votes.items()
    ]
    proposals.sort(key=lambda item: (-item.vote_count, item.cell_i, item.cell_j))
    status: Literal["completed", "budget_exhausted"] = (
        "completed" if budget_reason == "none" else "budget_exhausted"
    )
    return WedgeProposalResult(
        proposals=tuple(proposals),
        observations=obs,
        catalog_keys=tuple(item.key for item in objects),
        catalog_types=tuple(item.normalized_type for item in objects),
        catalog_xy_m=catalog_xy,
        cell_size_m=float(cell_size_m),
        max_range_m=float(max_range_m),
        angular_tolerance_deg=float(angular_tolerance_deg),
        source_pairs_total=source_total,
        source_pairs_processed=processed_pairs,
        candidate_cells_total=len(votes),
        generation_status=status,
        budget_reason=budget_reason,
    )


def generate_geographic_reverse_wedge_proposals(
    observations: tuple[WedgeObservation, ...] | list[WedgeObservation],
    catalog: tuple[GeographicWedgeCatalogObject, ...] | list[GeographicWedgeCatalogObject],
    *,
    frame: LocalFrame,
    max_range_m: float,
    cell_size_m: float = 250.0,
    angular_tolerance_deg: float = 0.5,
    max_grid_bearing_error_deg: float,
    max_source_pairs: int = 100_000,
    max_candidate_cells: int = 100_000,
) -> WedgeProposalResult:
    """Adapt geographic records into one caller-selected local projected tile.

    Use a separate LocalFrame for each coast tile and supply a qualified
    upper bound on true-north versus grid-bearing discrepancy over that tile.
    The bound widens coarse rays; original bearings and geographic positions
    remain available for exact geodesic scoring downstream. Proposals never
    certify exclusions.
    """
    if not isinstance(frame, LocalFrame):
        raise TypeError("frame must be a LocalFrame.")
    if not isfinite(max_grid_bearing_error_deg) or not 0.0 <= max_grid_bearing_error_deg < 90.0:
        raise ValueError("max_grid_bearing_error_deg must be finite and in [0, 90).")
    effective_tolerance_deg = angular_tolerance_deg + max_grid_bearing_error_deg
    if effective_tolerance_deg >= 90.0:
        raise ValueError("angular tolerance plus grid-bearing bound must be below 90 degrees.")
    geographic = tuple(sorted(catalog, key=lambda item: item.key))
    if any(not isinstance(item, GeographicWedgeCatalogObject) for item in geographic):
        raise TypeError("catalog must contain GeographicWedgeCatalogObject records.")
    if len({item.key for item in geographic}) != len(geographic):
        raise ValueError("catalog keys must be unique.")
    local_catalog = tuple(
        WedgeCatalogObject(
            item.key,
            *frame.to_local((item.longitude_deg, item.latitude_deg)),
            normalized_type=item.normalized_type,
        )
        for item in geographic
    )
    result = generate_reverse_wedge_proposals(
        observations,
        local_catalog,
        max_range_m=max_range_m,
        cell_size_m=cell_size_m,
        angular_tolerance_deg=effective_tolerance_deg,
        max_source_pairs=max_source_pairs,
        max_candidate_cells=max_candidate_cells,
    )
    lonlat = np.asarray(
        [(item.longitude_deg, item.latitude_deg) for item in geographic], dtype=np.float64
    ).reshape((-1, 2))
    lonlat.setflags(write=False)
    return replace(
        result,
        coordinate_model="local_projected_tile",
        frame_origin_lonlat=frame.origin_lonlat,
        frame_crs=frame.projected_crs.to_string(),
        catalog_lonlat_deg=lonlat,
        grid_bearing_error_bound_deg=float(max_grid_bearing_error_deg),
    )


def generate_geographic_reverse_wedge_proposals_complete(
    observations: tuple[WedgeObservation, ...] | list[WedgeObservation],
    catalog: tuple[GeographicWedgeCatalogObject, ...] | list[GeographicWedgeCatalogObject],
    *,
    frame: LocalFrame,
    max_range_m: float,
    cell_size_m: float = 250.0,
    angular_tolerance_deg: float = 0.5,
    max_grid_bearing_error_deg: float,
    max_source_pairs: int = 100_000,
    max_candidate_cells: int = 100_000,
    source_chunk_size: int = 4_096,
    collect_metadata: bool = False,
) -> WedgeProposalResult:
    """Generate complete local-tile votes while retaining geographic metadata."""
    if not isinstance(frame, LocalFrame):
        raise TypeError("frame must be a LocalFrame.")
    if not isfinite(max_grid_bearing_error_deg) or not 0.0 <= max_grid_bearing_error_deg < 90.0:
        raise ValueError("max_grid_bearing_error_deg must be finite and in [0, 90).")
    effective_tolerance_deg = angular_tolerance_deg + max_grid_bearing_error_deg
    if effective_tolerance_deg >= 90.0:
        raise ValueError("angular tolerance plus grid-bearing bound must be below 90 degrees.")
    geographic = tuple(sorted(catalog, key=lambda item: item.key))
    if any(not isinstance(item, GeographicWedgeCatalogObject) for item in geographic):
        raise TypeError("catalog must contain GeographicWedgeCatalogObject records.")
    if len({item.key for item in geographic}) != len(geographic):
        raise ValueError("catalog keys must be unique.")
    local_catalog = tuple(
        WedgeCatalogObject(
            item.key,
            *frame.to_local((item.longitude_deg, item.latitude_deg)),
            normalized_type=item.normalized_type,
        )
        for item in geographic
    )
    result = generate_reverse_wedge_proposals_complete(
        observations,
        local_catalog,
        max_range_m=max_range_m,
        cell_size_m=cell_size_m,
        angular_tolerance_deg=effective_tolerance_deg,
        max_source_pairs=max_source_pairs,
        max_candidate_cells=max_candidate_cells,
        source_chunk_size=source_chunk_size,
        collect_metadata=collect_metadata,
    )
    lonlat = np.asarray(
        [(item.longitude_deg, item.latitude_deg) for item in geographic], dtype=np.float64
    ).reshape((-1, 2))
    lonlat.setflags(write=False)
    return replace(
        result,
        coordinate_model="local_projected_tile",
        frame_origin_lonlat=frame.origin_lonlat,
        frame_crs=frame.projected_crs.to_string(),
        catalog_lonlat_deg=lonlat,
        grid_bearing_error_bound_deg=float(max_grid_bearing_error_deg),
    )


__all__ = [
    "WedgeObservation",
    "WedgeCatalogObject",
    "GeographicWedgeCatalogObject",
    "WedgeProposal",
    "WedgeProposalResult",
    "generate_reverse_wedge_proposals",
    "generate_reverse_wedge_proposals_complete",
    "generate_geographic_reverse_wedge_proposals",
    "generate_geographic_reverse_wedge_proposals_complete",
]
