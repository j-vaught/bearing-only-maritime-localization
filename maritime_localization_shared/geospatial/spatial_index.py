"""Dependency-light spatial queries for local east/north meter coordinates.

The index uses a uniform grid to reduce the number of points checked for each
radius query. It returns indices into the original point sequence, preserving
duplicate points and stable input identity.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from math import floor, isfinite

Point2D = tuple[float, float]


class SpatialIndex2D:
    """Uniform-grid index for radius queries over projected EN coordinates.

    Coordinates and radii are in meters. Points are stored as
    ``(east_m, north_m)`` pairs. The index is immutable after construction.

    Args:
        points: Input points in stable order. Query results refer to their
            integer indices in this sequence.
        cell_size_m: Positive grid-cell width. Smaller cells reduce the number
            of distance checks at the cost of visiting more cells per query.
    """

    def __init__(self, points: Iterable[Point2D], cell_size_m: float = 1_000.0) -> None:
        if not isfinite(cell_size_m) or cell_size_m <= 0:
            raise ValueError("cell_size_m must be finite and positive.")

        self._cell_size_m = float(cell_size_m)
        self._points: tuple[Point2D, ...] = tuple(
            self._validate_point(point, f"points[{index}]") for index, point in enumerate(points)
        )
        buckets: defaultdict[tuple[int, int], list[int]] = defaultdict(list)
        for index, (east_m, north_m) in enumerate(self._points):
            buckets[self._cell(east_m, north_m)].append(index)
        self._buckets = {cell: tuple(indices) for cell, indices in buckets.items()}

    @staticmethod
    def _validate_point(point: Point2D, name: str) -> Point2D:
        try:
            east_m, north_m = point
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must contain exactly two coordinates.") from exc
        if not isfinite(east_m) or not isfinite(north_m):
            raise ValueError(f"{name} coordinates must be finite.")
        return float(east_m), float(north_m)

    def _cell(self, east_m: float, north_m: float) -> tuple[int, int]:
        return floor(east_m / self._cell_size_m), floor(north_m / self._cell_size_m)

    @property
    def points(self) -> tuple[Point2D, ...]:
        """Return indexed points in their original order."""
        return self._points

    @property
    def cell_size_m(self) -> float:
        """Return the grid-cell width in meters."""
        return self._cell_size_m

    def query_radius(self, east_m: float, north_m: float, radius_m: float) -> tuple[int, ...]:
        """Return input indices whose points are within ``radius_m``.

        The radius includes its boundary. Results are ordered by input index,
        not by distance. A zero radius returns points exactly at the query
        coordinate.
        """
        if not isfinite(east_m) or not isfinite(north_m):
            raise ValueError("Query coordinates must be finite.")
        if not isfinite(radius_m) or radius_m < 0:
            raise ValueError("radius_m must be finite and nonnegative.")

        east_m = float(east_m)
        north_m = float(north_m)
        radius_m = float(radius_m)
        min_cell_x, min_cell_y = self._cell(east_m - radius_m, north_m - radius_m)
        max_cell_x, max_cell_y = self._cell(east_m + radius_m, north_m + radius_m)
        radius_squared = radius_m * radius_m
        result: list[int] = []

        for cell_x in range(min_cell_x, max_cell_x + 1):
            for cell_y in range(min_cell_y, max_cell_y + 1):
                for index in self._buckets.get((cell_x, cell_y), ()):
                    point_east_m, point_north_m = self._points[index]
                    delta_east = point_east_m - east_m
                    delta_north = point_north_m - north_m
                    if delta_east * delta_east + delta_north * delta_north <= radius_squared:
                        result.append(index)

        result.sort()
        return tuple(result)
