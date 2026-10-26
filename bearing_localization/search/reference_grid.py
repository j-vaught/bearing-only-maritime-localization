"""Exhaustive, deterministic reference search over a bounded candidate grid.

The certificate produced here applies only to the enumerated finite grid. It
makes no claim about unsampled points or continuous-space optimality.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from math import floor, isfinite
from time import perf_counter

from maritime_localization_shared.contracts.inputs_v2 import CatalogV2, MeasurementSetV2
from maritime_localization_shared.contracts.localization import (
    Hypothesis,
    LocalizationResult,
)
from maritime_localization_shared.geospatial.vector_map import VectorMap

from .config import SearchConfig
from .scoring import CandidateScorer

GridPoint = tuple[float, float]
GridIndex = tuple[int, int]


@dataclass(frozen=True, slots=True)
class CandidateCounts:
    """Counts for every disposition of grid points, including unvisited points."""

    total: int
    evaluated: int
    scored: int
    invalid_water: int
    unresolved_coverage: int
    unresolved_model: int
    unevaluated: int

    def __post_init__(self) -> None:
        values = (
            self.total,
            self.evaluated,
            self.scored,
            self.invalid_water,
            self.unresolved_coverage,
            self.unresolved_model,
            self.unevaluated,
        )
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError("Candidate counts must be nonnegative integers.")
        if self.evaluated + self.unevaluated != self.total:
            raise ValueError("Evaluated and unevaluated counts must sum to total.")
        if (
            self.scored + self.invalid_water + self.unresolved_coverage + self.unresolved_model
            != self.evaluated
        ):
            raise ValueError("Candidate dispositions must sum to evaluated.")


@dataclass(frozen=True, slots=True)
class SpatialMode:
    """One locally minimal spatial mode and its tied grid points."""

    hypothesis: Hypothesis
    member_count: int
    grid_indices: tuple[GridIndex, ...]


@dataclass(frozen=True, slots=True)
class ReferenceGridResult:
    """Exhaustive-grid outcome with counts and the shared localization contract."""

    localization_result: LocalizationResult
    candidate_counts: CandidateCounts
    grid_shape: tuple[int, int]
    visited_points: tuple[GridPoint, ...]
    modes: tuple[SpatialMode, ...]

    @property
    def hypotheses(self) -> tuple[Hypothesis, ...]:
        return self.localization_result.hypotheses

    @property
    def decision_status(self) -> str:
        return self.localization_result.decision_status

    @property
    def search_status(self) -> str:
        return self.localization_result.search_status

    @property
    def certificate_scope(self) -> str:
        return self.localization_result.certificate_scope

    @property
    def elapsed_s(self) -> float:
        return self.localization_result.elapsed_s


def _axis_count(minimum: float, maximum: float, spacing_m: float) -> int:
    span = maximum - minimum
    intervals = floor(span / spacing_m)
    # Treat a nearly integral quotient as an aligned boundary so round-off does
    # not create a duplicate final coordinate.
    if abs(span - intervals * spacing_m) <= max(1e-12, abs(span) * 1e-12):
        return intervals + 1
    return intervals + 2


def _axis_value(minimum: float, maximum: float, spacing_m: float, index: int, count: int) -> float:
    if index == count - 1:
        return maximum
    value = minimum + index * spacing_m
    return min(value, maximum)


def grid_shape(bounds_xy: tuple[float, float, float, float], spacing_m: float) -> tuple[int, int]:
    """Return inclusive grid dimensions without allocating the point sequence."""
    _validate_grid(bounds_xy, spacing_m)
    xmin, xmax, ymin, ymax = bounds_xy
    return _axis_count(xmin, xmax, spacing_m), _axis_count(ymin, ymax, spacing_m)


def _validate_grid(bounds_xy: tuple[float, float, float, float], spacing_m: float) -> None:
    if len(bounds_xy) != 4 or not all(isfinite(value) for value in bounds_xy):
        raise ValueError("bounds_xy must contain four finite values.")
    xmin, xmax, ymin, ymax = bounds_xy
    if xmin > xmax or ymin > ymax:
        raise ValueError("bounds_xy must have nonnegative width and height.")
    if not isfinite(spacing_m) or spacing_m <= 0.0:
        raise ValueError("spacing_m must be finite and positive.")


def enumerate_grid_points(
    bounds_xy: tuple[float, float, float, float], spacing_m: float
) -> Iterator[GridPoint]:
    """Yield sorted points with inclusive bounds and final partial intervals.

    Coordinates are ordered east first and then north. Each axis advances by
    ``spacing_m`` from its minimum and appends its exact maximum when the step
    does not land there. This deterministic final partial interval guarantees
    both requested bounds are represented without extrapolating outside them.
    """
    nx, ny = grid_shape(bounds_xy, spacing_m)
    xmin, xmax, ymin, ymax = bounds_xy
    for east_index in range(nx):
        east_m = _axis_value(xmin, xmax, spacing_m, east_index, nx)
        for north_index in range(ny):
            north_m = _axis_value(ymin, ymax, spacing_m, north_index, ny)
            yield east_m, north_m


def _tied(left: float, right: float, *, absolute: float, relative: float) -> bool:
    return abs(left - right) <= max(absolute, relative * max(abs(left), abs(right)))


def _local_modes(
    scored: dict[GridIndex, Hypothesis],
    *,
    nx: int,
    ny: int,
    absolute_tolerance: float,
    relative_tolerance: float,
) -> tuple[SpatialMode, ...]:
    """Find 8-neighbor local minima and collapse adjacent tied plateaus."""
    minima: set[GridIndex] = set()
    for index, hypothesis in scored.items():
        east_index, north_index = index
        neighbors = (
            scored.get((east_index + dx, north_index + dy))
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
            if (dx != 0 or dy != 0) and 0 <= east_index + dx < nx and 0 <= north_index + dy < ny
        )
        adjacent = tuple(neighbor for neighbor in neighbors if neighbor is not None)
        no_lower_neighbor = all(
            hypothesis.score <= neighbor.score
            or _tied(
                hypothesis.score,
                neighbor.score,
                absolute=absolute_tolerance,
                relative=relative_tolerance,
            )
            for neighbor in adjacent
        )
        canonical_plateau_point = all(
            not _tied(
                hypothesis.score,
                neighbor.score,
                absolute=absolute_tolerance,
                relative=relative_tolerance,
            )
            or index < neighbor_index
            for neighbor_index in (
                (east_index + dx, north_index + dy)
                for dx in (-1, 0, 1)
                for dy in (-1, 0, 1)
                if (dx != 0 or dy != 0) and 0 <= east_index + dx < nx and 0 <= north_index + dy < ny
            )
            if (neighbor := scored.get(neighbor_index)) is not None
        )
        if no_lower_neighbor and canonical_plateau_point:
            minima.add(index)

    # Expand each canonical minimum across its connected equal-score plateau.
    remaining = set(minima)
    components: list[tuple[GridIndex, ...]] = []
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        component = {seed}
        frontier = [seed]
        seed_score = scored[seed].score
        while frontier:
            east_index, north_index = frontier.pop()
            adjacent = (
                (east_index + dx, north_index + dy)
                for dx in (-1, 0, 1)
                for dy in (-1, 0, 1)
                if (dx != 0 or dy != 0) and 0 <= east_index + dx < nx and 0 <= north_index + dy < ny
            )
            for other in adjacent:
                if (
                    other in scored
                    and other not in component
                    and _tied(
                        seed_score,
                        scored[other].score,
                        absolute=absolute_tolerance,
                        relative=relative_tolerance,
                    )
                ):
                    component.add(other)
                    remaining.discard(other)
                    frontier.append(other)
        components.append(tuple(sorted(component)))

    modes: list[SpatialMode] = []
    for component in components:
        representative_index = min(component, key=lambda index: (scored[index].score, index))
        modes.append(
            SpatialMode(
                hypothesis=scored[representative_index],
                member_count=len(component),
                grid_indices=component,
            )
        )
    modes.sort(
        key=lambda mode: (mode.hypothesis.score, mode.hypothesis.east_m, mode.hypothesis.north_m)
    )
    return tuple(modes)


def search_reference_grid(
    measurements: MeasurementSetV2,
    catalog: CatalogV2,
    vector_map: VectorMap,
    config: SearchConfig,
    *,
    spacing_m: float = 10.0,
) -> ReferenceGridResult:
    """Score every declared grid point until completion or candidate budget.

    The candidate scorer is constructed once and reuses its spatial index.
    Water-invalid points are counted and excluded. Unknown coverage or model
    points remain explicit unresolved outcomes and prevent a finite-set
    certificate. Decisions are always ambiguous or no-fix until independent
    calibration authorizes acceptance.
    """
    if config.bounds_xy is None:
        raise ValueError("Reference grid search requires config.bounds_xy.")
    shape = grid_shape(config.bounds_xy, spacing_m)
    total = shape[0] * shape[1]
    budget = min(config.candidate_budget, total)
    scorer = CandidateScorer(measurements, catalog, vector_map, config)
    started = perf_counter()

    status_counts = {
        "scored": 0,
        "invalid_water": 0,
        "unresolved_coverage": 0,
        "unresolved_model": 0,
    }
    points: list[GridPoint] = []
    scored: dict[GridIndex, Hypothesis] = {}
    nx, ny = shape

    for point in enumerate_grid_points(config.bounds_xy, spacing_m):
        if len(points) >= budget:
            break
        points.append(point)
        east_index = len(points) - 1
        # The generator is east-major, so recover row/column exactly from its
        # visitation ordinal without storing the entire finite grid.
        grid_index = (east_index // ny, east_index % ny)
        evaluation = scorer.evaluate(point)
        status_counts[evaluation.status] += 1
        if evaluation.status == "scored":
            assert evaluation.hypothesis is not None
            scored[grid_index] = evaluation.hypothesis

    evaluated = len(points)
    unevaluated = total - evaluated
    counts = CandidateCounts(
        total=total,
        evaluated=evaluated,
        scored=status_counts["scored"],
        invalid_water=status_counts["invalid_water"],
        unresolved_coverage=status_counts["unresolved_coverage"],
        unresolved_model=status_counts["unresolved_model"],
        unevaluated=unevaluated,
    )
    complete = unevaluated == 0
    unresolved = counts.unresolved_coverage + counts.unresolved_model
    certificate_scope = "finite_candidate_set" if complete and unresolved == 0 else "none"
    modes = _local_modes(
        scored,
        nx=nx,
        ny=ny,
        absolute_tolerance=config.absolute_score_tolerance,
        relative_tolerance=config.relative_score_tolerance,
    )
    hypotheses = tuple(mode.hypothesis for mode in modes)
    search_status = "completed" if complete else "budget_exhausted"
    decision_status = "ambiguous" if hypotheses else "no_fix"
    localization = LocalizationResult(
        hypotheses=hypotheses,
        elapsed_s=perf_counter() - started,
        decision_status=decision_status,
        search_status=search_status,
        certificate_scope=certificate_scope,
        budget_limit=config.candidate_budget,
        budget_used=evaluated,
        report_count=len(measurements.measurements),
        objective_version=config.objective_version,
    )
    return ReferenceGridResult(
        localization_result=localization,
        candidate_counts=counts,
        grid_shape=shape,
        visited_points=tuple(points),
        modes=modes,
    )


# Short aliases are convenient for independent reference-implementation comparisons.
enumerate_grid = enumerate_grid_points


__all__ = [
    "CandidateCounts",
    "ReferenceGridResult",
    "SpatialMode",
    "enumerate_grid",
    "enumerate_grid_points",
    "grid_shape",
    "search_reference_grid",
]
