"""Finite-range reverse-bearing votes for sparse position proposals.

Votes only prioritize grid cells. They do not score observation/catalog
associations and cannot certify that an excluded cell is impossible.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import atan2, cos, degrees, floor, hypot, isfinite, radians, sin
from typing import Literal

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True, slots=True)
class WedgeObservation:
    """One retained true-north bearing and caller-stable report index."""

    report_index: int
    bearing_deg: float

    def __post_init__(self) -> None:
        if type(self.report_index) is not int or self.report_index < 0:
            raise ValueError("report_index must be a nonnegative integer.")
        if not isfinite(self.bearing_deg) or not 0.0 <= self.bearing_deg < 360.0:
            raise ValueError("bearing_deg must be finite and in [0, 360).")


@dataclass(frozen=True, slots=True)
class WedgeCatalogObject:
    """A stable catalog key at east/north meter coordinates."""

    key: str
    east_m: float
    north_m: float

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not self.key.strip():
            raise ValueError("key must be a nonempty stable string.")
        if not isfinite(self.east_m) or not isfinite(self.north_m):
            raise ValueError("Catalog coordinates must be finite.")


@dataclass(frozen=True, slots=True)
class WedgeProposal:
    """One quantized cell with unique-observation vote support."""

    candidate_id: str
    cell_i: int
    cell_j: int
    east_m: float
    north_m: float
    vote_count: int
    observation_indices: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class WedgeProposalResult:
    """Finite proposals plus the exact inputs and budget accounting for parity."""

    proposals: tuple[WedgeProposal, ...]
    observations: tuple[WedgeObservation, ...]
    catalog_keys: tuple[str, ...]
    catalog_xy_m: NDArray[np.float64]
    cell_size_m: float
    max_range_m: float
    angular_tolerance_deg: float
    source_pairs_total: int
    source_pairs_processed: int
    candidate_cells_total: int
    generation_status: Literal["completed", "budget_exhausted"]
    budget_reason: Literal["none", "source_pairs", "candidate_cells"]

    @property
    def observations_array(self) -> NDArray[np.float64]:
        """Return ``(report_index, original_bearing_deg)`` in stable order."""
        result = np.asarray(
            [(item.report_index, item.bearing_deg) for item in self.observations],
            dtype=np.float64,
        ).reshape((-1, 2))
        result.setflags(write=False)
        return result

    @property
    def catalog_metadata(self) -> dict[str, object]:
        """Return stable catalog keys and copied planar coordinates for consumers."""
        coords = np.array(self.catalog_xy_m, dtype=np.float64, copy=True)
        coords.setflags(write=False)
        return {
            "keys": self.catalog_keys,
            "coordinates_en_m": coords,
            "coordinate_model": "planar_meter_frame",
        }

    @property
    def complete(self) -> bool:
        """Whether every observation/catalog source pair was processed."""
        return self.generation_status == "completed"


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
) -> set[tuple[int, int]]:
    """Rasterize a finite reverse ray and test quantized cell centers."""
    half_diagonal = cell_size_m * 2.0**0.5 / 2.0
    step_m = cell_size_m / 2.0
    reverse = radians((bearing_deg + 180.0) % 360.0)
    direction_east, direction_north = sin(reverse), cos(reverse)
    samples: set[tuple[int, int]] = set()
    sample_distance_m = 0.0
    final_distance_m = max_range_m + half_diagonal

    while sample_distance_m <= final_distance_m:
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
                    quantization_deg = degrees(atan2(half_diagonal, distance_m))
                    if _angle_error_deg(center_bearing, bearing_deg) > (
                        angular_tolerance_deg + quantization_deg
                    ):
                        continue
                samples.add((cell_i, cell_j))
        sample_distance_m += step_m
    return samples


def generate_reverse_wedge_proposals(
    observations: tuple[WedgeObservation, ...] | list[WedgeObservation],
    catalog: tuple[WedgeCatalogObject, ...] | list[WedgeCatalogObject],
    *,
    max_range_m: float,
    cell_size_m: float = 250.0,
    angular_tolerance_deg: float = 0.5,
    max_source_pairs: int = 1_000_000,
    max_candidate_cells: int = 100_000,
) -> WedgeProposalResult:
    """Generate deterministic sparse reverse-bearing cell votes.

    Each observation contributes at most one vote to any cell, regardless of
    how many catalog objects claim it. Inputs are ordered by stable report
    index and key before traversal, so partitioned/replayed input batches with
    the same records yield identical cell IDs and vote sets. Python integer
    bitsets avoid fixed-width accumulator overflow. A budget limit is returned
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
    # A dictionary maps cell identity to an integer observation bitset. This
    # stores unique votes compactly and uses unbounded Python integer arithmetic.
    votes: dict[tuple[int, int], int] = {}
    processed_pairs = 0
    budget_reason: Literal["none", "source_pairs", "candidate_cells"] = "none"
    obs_position = {item.report_index: index for index, item in enumerate(obs)}

    for observation in obs:
        bit = 1 << obs_position[observation.report_index]
        for source in objects:
            if processed_pairs >= max_source_pairs:
                budget_reason = "source_pairs"
                break
            source_cells = _candidate_cells_for_source(
                source.east_m,
                source.north_m,
                observation.bearing_deg,
                cell_size_m=cell_size_m,
                max_range_m=max_range_m,
                angular_tolerance_deg=angular_tolerance_deg,
            )
            for cell in sorted(source_cells):
                existing = votes.get(cell)
                if existing is None:
                    if len(votes) >= max_candidate_cells:
                        budget_reason = "candidate_cells"
                        break
                    votes[cell] = bit
                else:
                    votes[cell] = existing | bit
            if budget_reason == "candidate_cells":
                break
            processed_pairs += 1
        if budget_reason != "none":
            break

    # A source-pair cap is exhausted only when unprocessed pairs remain.
    if budget_reason == "none" and processed_pairs < source_total:
        budget_reason = "source_pairs"
    proposals: list[WedgeProposal] = []
    for (cell_i, cell_j), bits in votes.items():
        report_indices = tuple(
            observation.report_index for index, observation in enumerate(obs) if bits & (1 << index)
        )
        proposals.append(
            WedgeProposal(
                candidate_id=f"cell:{cell_i}:{cell_j}",
                cell_i=cell_i,
                cell_j=cell_j,
                east_m=(cell_i + 0.5) * cell_size_m,
                north_m=(cell_j + 0.5) * cell_size_m,
                vote_count=bits.bit_count(),
                observation_indices=report_indices,
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


__all__ = [
    "WedgeObservation",
    "WedgeCatalogObject",
    "WedgeProposal",
    "WedgeProposalResult",
    "generate_reverse_wedge_proposals",
]
