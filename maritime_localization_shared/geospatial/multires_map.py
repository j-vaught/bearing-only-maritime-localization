"""Conservative deterministic multiresolution summaries of a :class:`VectorMap`.

A summary only certifies a cell when exact polygon predicates prove the entire
closed rectangular cell has one known classification. Mixed geometry and map
coverage are kept uncertain, so coarse summaries can prioritize queries but
cannot erase narrow water passages or classify unknown regions as land.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, floor, isfinite
from typing import Literal

import numpy as np
from numpy.typing import NDArray
from shapely.geometry import box

from maritime_localization_shared.geospatial.vector_map import VectorMap, WaterStatus

CellStatus = Literal["definitely_land", "definitely_water", "uncertain"]


@dataclass(frozen=True, slots=True)
class CellQuery:
    """Summary result for one point at one requested grid resolution."""

    status: CellStatus
    resolution_m: float
    bounds_m: tuple[float, float, float, float]
    exact_status: WaterStatus | None = None


@dataclass(frozen=True, slots=True)
class _Grid:
    resolution_m: float
    west_m: float
    south_m: float
    rows: int
    columns: int
    statuses: NDArray[np.uint8]


class MultiResolutionMap:
    """Conservative rectangular-cell summaries for a projected ``VectorMap``.

    ``bounds_m`` is ``(west, south, east, north)`` and resolutions are positive
    metre cell sizes. Grid origins are fixed at the supplied west/south bounds;
    grid dimensions use ceiling division, making the layer bounds deterministic.
    The last row or column may extend beyond the requested outer bounds, but
    queries outside the requested bounds remain unknown.

    Each closed cell is tested against the map's exact polygon geometry. Land is
    certified only when known land covers the *whole closed cell*, with known
    coverage also covering it and no explicit unknown geometry touching it.
    This is sufficient because every point in that cell is then in known land.
    Water is certified only when coverage covers the whole closed cell and both
    known land and unknown coverage are disjoint from it. Thus every point is
    covered, known, and outside land. Shapely's full-geometry ``covers`` and
    ``disjoint`` predicates are used; corners or pixel-center samples are never
    used as a proof. A narrow channel, boundary gap, hole, or coverage seam in a
    mixed cell therefore makes that cell uncertain instead of blocking it.
    """

    _DEFINITELY_LAND = np.uint8(1)
    _DEFINITELY_WATER = np.uint8(2)
    _UNCERTAIN = np.uint8(0)

    def __init__(
        self,
        vector_map: VectorMap,
        *,
        bounds_m: tuple[float, float, float, float],
        resolutions_m: tuple[float, ...] | list[float],
    ) -> None:
        if not isinstance(vector_map, VectorMap):
            raise TypeError("vector_map must be a VectorMap instance.")
        west, south, east, north = _bounds(bounds_m)
        resolutions = tuple(float(value) for value in resolutions_m)
        if not resolutions or any(not isfinite(value) or value <= 0.0 for value in resolutions):
            raise ValueError("resolutions_m must contain positive finite cell sizes.")
        if len(set(resolutions)) != len(resolutions):
            raise ValueError("resolutions_m must not contain duplicates.")

        self._map = vector_map
        self._bounds = (west, south, east, north)
        self._resolutions = resolutions
        self._grids = {
            resolution: self._build_grid(resolution, west, south, east, north)
            for resolution in resolutions
        }

    @property
    def bounds_m(self) -> tuple[float, float, float, float]:
        """Requested query bounds as ``(west, south, east, north)`` in metres."""
        return self._bounds

    @property
    def resolutions_m(self) -> tuple[float, ...]:
        """Configured cell sizes in the caller-supplied deterministic order."""
        return self._resolutions

    def classify(
        self,
        point_xy_m: tuple[float, float],
        *,
        resolution_m: float,
        exact_check: bool = False,
    ) -> CellQuery:
        """Return the containing-cell summary, optionally with exact point status.

        ``exact_check`` is diagnostic only. It adds the vector map's exact point
        classification to the result without changing the summary decision.
        Points outside configured bounds or in the uncovered outer portion of a
        ceiling-sized grid return uncertain with no exact status unless requested.
        """
        x, y = _point_xy(point_xy_m)
        try:
            grid = self._grids[float(resolution_m)]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("resolution_m must be one of the configured resolutions.") from exc

        if not (self._bounds[0] <= x < self._bounds[2] and self._bounds[1] <= y < self._bounds[3]):
            exact = self._map.point_water_status((x, y)) if exact_check else None
            return CellQuery("uncertain", grid.resolution_m, (x, y, x, y), exact)

        column = floor((x - grid.west_m) / grid.resolution_m)
        row = floor((y - grid.south_m) / grid.resolution_m)
        left = grid.west_m + column * grid.resolution_m
        bottom = grid.south_m + row * grid.resolution_m
        cell_bounds = (left, bottom, left + grid.resolution_m, bottom + grid.resolution_m)
        status = _decode_status(grid.statuses[row, column])
        exact = self._map.point_water_status((x, y)) if exact_check else None
        return CellQuery(status, grid.resolution_m, cell_bounds, exact)

    def summary_array(self, resolution_m: float) -> NDArray[np.str_]:
        """Return a read-only ``[row, column]`` array of named cell statuses."""
        try:
            grid = self._grids[float(resolution_m)]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("resolution_m must be one of the configured resolutions.") from exc
        names = np.full(grid.statuses.shape, "uncertain", dtype="<U16")
        names[grid.statuses == self._DEFINITELY_LAND] = "definitely_land"
        names[grid.statuses == self._DEFINITELY_WATER] = "definitely_water"
        names.setflags(write=False)
        return names

    def compare_points(
        self,
        points_xy_m: NDArray[np.float64] | list[tuple[float, float]],
        *,
        resolution_m: float,
    ) -> dict[str, int]:
        """Count exact point disagreements against definitive cell summaries.

        Uncertain cells are not counted as disagreements because they make no
        definitive claim. The returned counters expose the brute-force diagnostic
        requested for validation without changing query behavior.
        """
        points = np.asarray(points_xy_m, dtype=np.float64)
        if points.ndim != 2 or points.shape[1] != 2:
            raise ValueError("points_xy_m must have shape (n, 2).")
        counts = {"checked": 0, "definitive": 0, "disagreements": 0}
        for x, y in points:
            result = self.classify(
                (float(x), float(y)), resolution_m=resolution_m, exact_check=True
            )
            counts["checked"] += 1
            if result.status == "uncertain":
                continue
            counts["definitive"] += 1
            expected = "land" if result.status == "definitely_land" else "water"
            counts["disagreements"] += result.exact_status != expected
        return counts

    def _build_grid(
        self,
        resolution: float,
        west: float,
        south: float,
        east: float,
        north: float,
    ) -> _Grid:
        columns = ceil((east - west) / resolution)
        rows = ceil((north - south) / resolution)
        statuses = np.full((rows, columns), self._UNCERTAIN, dtype=np.uint8)
        # Coverage and unknown are maintained by VectorMap as exact polygonal
        # geometries. They are intentionally used here instead of inferring full
        # cell coverage from point queries or cell corners.
        coverage = self._map._coverage
        unknown = self._map._unknown
        land = self._map.land_geometry
        if coverage is None:
            statuses.setflags(write=False)
            return _Grid(resolution, west, south, rows, columns, statuses)

        for row in range(rows):
            bottom = south + row * resolution
            for column in range(columns):
                left = west + column * resolution
                cell = box(left, bottom, left + resolution, bottom + resolution)
                if not coverage.covers(cell):
                    continue
                if unknown is not None and not unknown.disjoint(cell):
                    continue
                if land.covers(cell):
                    statuses[row, column] = self._DEFINITELY_LAND
                elif land.disjoint(cell):
                    statuses[row, column] = self._DEFINITELY_WATER
        statuses.setflags(write=False)
        return _Grid(resolution, west, south, rows, columns, statuses)


def _bounds(bounds_m: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    try:
        west, south, east, north = bounds_m
    except (TypeError, ValueError) as exc:
        raise ValueError("bounds_m must be (west, south, east, north).") from exc
    if any(not isfinite(value) for value in (west, south, east, north)):
        raise ValueError("bounds_m coordinates must be finite.")
    if east <= west or north <= south:
        raise ValueError("bounds_m must have positive width and height.")
    return float(west), float(south), float(east), float(north)


def _point_xy(point_xy_m: tuple[float, float]) -> tuple[float, float]:
    try:
        x, y = point_xy_m
    except (TypeError, ValueError) as exc:
        raise ValueError("point_xy_m must contain two finite coordinates.") from exc
    if not isfinite(x) or not isfinite(y):
        raise ValueError("point_xy_m must contain two finite coordinates.")
    return float(x), float(y)


def _decode_status(value: np.uint8) -> CellStatus:
    if value == MultiResolutionMap._DEFINITELY_LAND:
        return "definitely_land"
    if value == MultiResolutionMap._DEFINITELY_WATER:
        return "definitely_water"
    return "uncertain"
