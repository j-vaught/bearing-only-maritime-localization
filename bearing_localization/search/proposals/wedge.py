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

from maritime_localization_shared.geospatial.coordinates import LocalFrame


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


__all__ = [
    "WedgeObservation",
    "WedgeCatalogObject",
    "GeographicWedgeCatalogObject",
    "WedgeProposal",
    "WedgeProposalResult",
    "generate_reverse_wedge_proposals",
    "generate_geographic_reverse_wedge_proposals",
]
