"""Grid traversal for conservative line-of-sight checks over water masks."""

from __future__ import annotations

from collections.abc import Sequence
from math import floor, isfinite

from maritime_localization_shared.contracts.map_input import MapInput


def raster_line_of_sight(
    map_input: MapInput,
    start_xy_m: tuple[float, float],
    end_xy_m: tuple[float, float],
    *,
    nodata_mask: Sequence[Sequence[bool]] | None = None,
) -> bool:
    """Return whether a segment is visible through a row-major water raster.

    ``True`` mask cells are water and ``False`` cells are land. The ray is
    traversed with a supercover variant of Amanatides-Woo grid traversal, so
    cells touched at exact corner crossings are checked as well. Land in the
    start and end cells is permitted (for example, a target at the shoreline),
    while land in every traversed intermediate cell blocks visibility. A ray
    endpoint outside the raster is not visible. If supplied, ``nodata_mask``
    uses ``True`` for unknown cells, and any touched unknown cell blocks the
    ray, including at either endpoint.

    Coordinates use the projected metre frame and cell-edge origin defined by
    :class:`MapInput`. The raster extent is half-open on its east and north
    edges. Points and raster dimensions are validated before traversal.
    """
    if not _finite_xy(start_xy_m) or not _finite_xy(end_xy_m):
        raise ValueError("Line-of-sight endpoints must contain finite coordinates.")

    rows = map_input.height_cells
    cols = map_input.width_cells
    if nodata_mask is not None:
        if len(nodata_mask) != rows or any(len(row) != cols for row in nodata_mask):
            raise ValueError("nodata_mask dimensions must match the water mask.")
        if any(type(cell) is not bool for row in nodata_mask for cell in row):
            raise TypeError("nodata_mask cells must be bool values.")

    sx = (start_xy_m[0] - map_input.west_m) / map_input.cell_size_m
    sy = (start_xy_m[1] - map_input.south_m) / map_input.cell_size_m
    ex = (end_xy_m[0] - map_input.west_m) / map_input.cell_size_m
    ey = (end_xy_m[1] - map_input.south_m) / map_input.cell_size_m
    if not (_inside_grid(sx, sy, cols, rows) and _inside_grid(ex, ey, cols, rows)):
        return False

    dx = ex - sx
    dy = ey - sy
    ix = _directional_cell(sx, dx)
    iy = _directional_cell(sy, dy)
    end_ix = _directional_cell(ex, dx)
    end_iy = _directional_cell(ey, dy)
    endpoint_cells = {(ix, iy), (end_ix, end_iy)}

    def blocked(cell_x: int, cell_y: int) -> bool:
        if cell_x < 0 or cell_x >= cols or cell_y < 0 or cell_y >= rows:
            return True
        if nodata_mask is not None and nodata_mask[cell_y][cell_x]:
            return True
        is_endpoint_cell = (cell_x, cell_y) in endpoint_cells
        return not is_endpoint_cell and not map_input.water_mask[cell_y][cell_x]

    if blocked(ix, iy) or blocked(end_ix, end_iy):
        return False

    step_x = 1 if dx > 0 else -1 if dx < 0 else 0
    step_y = 1 if dy > 0 else -1 if dy < 0 else 0
    t_delta_x = abs(1.0 / dx) if step_x else float("inf")
    t_delta_y = abs(1.0 / dy) if step_y else float("inf")
    t_max_x = _first_boundary_parameter(sx, dx, ix, step_x)
    t_max_y = _first_boundary_parameter(sy, dy, iy, step_y)

    while (ix, iy) != (end_ix, end_iy):
        if _nearly_equal(t_max_x, t_max_y):
            # At a corner, the line touches both side-adjacent cells before
            # entering the diagonal cell. Checking them prevents diagonal leaks.
            if blocked(ix + step_x, iy) or blocked(ix, iy + step_y):
                return False
            ix += step_x
            iy += step_y
            t_max_x += t_delta_x
            t_max_y += t_delta_y
        elif t_max_x < t_max_y:
            ix += step_x
            t_max_x += t_delta_x
        else:
            iy += step_y
            t_max_y += t_delta_y
        if blocked(ix, iy):
            return False

    return True


def _finite_xy(point: tuple[float, float]) -> bool:
    return (
        isinstance(point, tuple)
        and len(point) == 2
        and all(
            isinstance(value, int | float) and not isinstance(value, bool) and isfinite(value)
            for value in point
        )
    )


def _inside_grid(x: float, y: float, width: int, height: int) -> bool:
    return 0.0 <= x < width and 0.0 <= y < height


def _directional_cell(coordinate: float, delta: float) -> int:
    """Choose the cell entered by the ray when a point lies on an edge."""
    nearest_edge = round(coordinate)
    if delta < 0 and abs(coordinate - nearest_edge) <= 1e-12:
        return nearest_edge - 1
    return floor(coordinate)


def _first_boundary_parameter(coordinate: float, delta: float, cell: int, step: int) -> float:
    if step > 0:
        return ((cell + 1) - coordinate) / delta
    if step < 0:
        return (cell - coordinate) / delta
    return float("inf")


def _nearly_equal(left: float, right: float) -> bool:
    if left == right:
        return True
    if not (isfinite(left) and isfinite(right)):
        return False
    return abs(left - right) <= 1e-12 * max(1.0, abs(left), abs(right))
