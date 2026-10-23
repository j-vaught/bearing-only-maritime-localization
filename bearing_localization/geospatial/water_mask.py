"""Fast point queries against a projected raster water mask.

Rows increase from south to north and columns increase from west to east. The
origin is the southwest edge of cell ``(0, 0)``. Points outside the raster and
points in invalid (for example, no-data) cells are conservatively treated as
not water.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import floor, isfinite

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True, slots=True, init=False)
class WaterMask:
    """Raster-backed water classification in a local projected coordinate frame.

    Args:
        water: Two-dimensional boolean array, indexed ``[row, column]``. True
            marks a cell as water.
        west_m: West edge of the first column, in projected meters.
        south_m: South edge of the first row, in projected meters.
        cell_size_m: Positive square-cell width and height, in meters.
        valid: Optional boolean array indicating cells with valid source data.
            Invalid cells are always classified as not water.

    Raster bounds are half-open: west and south edges are included, while east
    and north edges are excluded. This avoids assigning an edge point to a
    nonexistent cell.
    """

    water: NDArray[np.bool_]
    west_m: float
    south_m: float
    cell_size_m: float
    valid: NDArray[np.bool_]

    def __init__(
        self,
        water: NDArray[np.bool_],
        west_m: float,
        south_m: float,
        cell_size_m: float,
        valid: NDArray[np.bool_] | None = None,
    ) -> None:
        water_array = np.asarray(water)
        if water_array.ndim != 2 or not water_array.size:
            raise ValueError("water must be a nonempty two-dimensional array.")
        if water_array.dtype != np.bool_:
            raise TypeError("water must contain boolean values.")
        if not isfinite(west_m) or not isfinite(south_m):
            raise ValueError("Raster origin coordinates must be finite.")
        if not isfinite(cell_size_m) or cell_size_m <= 0:
            raise ValueError("cell_size_m must be finite and positive.")

        if valid is None:
            valid_array = np.ones(water_array.shape, dtype=np.bool_)
        else:
            valid_array = np.asarray(valid)
            if valid_array.shape != water_array.shape:
                raise ValueError("valid must have the same shape as water.")
            if valid_array.dtype != np.bool_:
                raise TypeError("valid must contain boolean values.")

        # Own immutable copies so external writes cannot silently alter queries.
        water_copy = np.array(water_array, dtype=np.bool_, copy=True)
        valid_copy = np.array(valid_array, dtype=np.bool_, copy=True)
        water_copy.setflags(write=False)
        valid_copy.setflags(write=False)

        object.__setattr__(self, "water", water_copy)
        object.__setattr__(self, "west_m", float(west_m))
        object.__setattr__(self, "south_m", float(south_m))
        object.__setattr__(self, "cell_size_m", float(cell_size_m))
        object.__setattr__(self, "valid", valid_copy)

    @property
    def width_cells(self) -> int:
        """Number of columns in the raster."""

        return int(self.water.shape[1])

    @property
    def height_cells(self) -> int:
        """Number of rows in the raster."""

        return int(self.water.shape[0])

    @property
    def bounds_m(self) -> tuple[float, float, float, float]:
        """Raster bounds as ``(west, south, east, north)`` in meters."""

        return (
            self.west_m,
            self.south_m,
            self.west_m + self.width_cells * self.cell_size_m,
            self.south_m + self.height_cells * self.cell_size_m,
        )

    def world_to_cell(self, x_m: float, y_m: float) -> tuple[int, int] | None:
        """Map a projected point to ``(row, column)``, or None if out of bounds.

        The point is assigned to the cell containing it. Coordinates at the
        outer east or north bound are outside the raster by definition.
        """

        if not isfinite(x_m) or not isfinite(y_m):
            return None
        col = floor((x_m - self.west_m) / self.cell_size_m)
        row = floor((y_m - self.south_m) / self.cell_size_m)
        if 0 <= row < self.height_cells and 0 <= col < self.width_cells:
            return row, col
        return None

    def world_to_cell_batch(self, points_xy_m: NDArray[np.float64]) -> NDArray[np.int64]:
        """Vectorized coordinate transform returning ``(row, column)`` pairs.

        Invalid or out-of-bounds points receive ``(-1, -1)``. Input must have
        shape ``(..., 2)`` with coordinates ordered ``(x, y)``.
        """

        points = self._validate_points(points_xy_m)
        shape = points.shape[:-1]
        flat = points.reshape(-1, 2)
        cells = np.full((flat.shape[0], 2), -1, dtype=np.int64)
        finite = np.isfinite(flat).all(axis=1)
        if finite.any():
            cols = np.floor((flat[finite, 0] - self.west_m) / self.cell_size_m).astype(np.int64)
            rows = np.floor((flat[finite, 1] - self.south_m) / self.cell_size_m).astype(np.int64)
            inside = (
                (rows >= 0) & (rows < self.height_cells) & (cols >= 0) & (cols < self.width_cells)
            )
            finite_indices = np.flatnonzero(finite)
            cells[finite_indices[inside], 0] = rows[inside]
            cells[finite_indices[inside], 1] = cols[inside]
        return cells.reshape(*shape, 2)

    def is_water(self, x_m: float, y_m: float) -> bool:
        """Return whether a point lies in a valid raster cell classified as water."""

        cell = self.world_to_cell(x_m, y_m)
        if cell is None:
            return False
        row, col = cell
        return bool(self.valid[row, col] and self.water[row, col])

    def is_water_batch(self, points_xy_m: NDArray[np.float64]) -> NDArray[np.bool_]:
        """Vectorized water query for points in ``(..., 2)`` ``(x, y)`` order.

        Outside and invalid/no-data cells return False. The output shape equals
        the input shape without its final coordinate dimension.
        """

        points = self._validate_points(points_xy_m)
        shape = points.shape[:-1]
        cells = self.world_to_cell_batch(points).reshape(-1, 2)
        result = np.zeros(cells.shape[0], dtype=np.bool_)
        inside = cells[:, 0] >= 0
        rows = cells[inside, 0]
        cols = cells[inside, 1]
        result[inside] = self.valid[rows, cols] & self.water[rows, cols]
        return result.reshape(shape)

    @staticmethod
    def _validate_points(points_xy_m: NDArray[np.float64]) -> NDArray[np.float64]:
        points = np.asarray(points_xy_m, dtype=np.float64)
        if points.ndim < 1 or points.shape[-1] != 2:
            raise ValueError("points must have shape (..., 2) in (x, y) order.")
        return points
