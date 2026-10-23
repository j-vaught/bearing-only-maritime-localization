"""Candidate-position grid generation for localization searches.

Candidate generation is deliberately independent from scoring and association.
It supplies a deterministic east/north grid and can optionally restrict that
grid to points classified as water by a raster or other batch query object.
"""

from __future__ import annotations

from math import floor, isclose, isfinite
from typing import Protocol

import numpy as np
from numpy.typing import NDArray


class WaterMaskProtocol(Protocol):
    """Minimal interface required to filter candidate positions by water."""

    def is_water_batch(self, points_xy_m: NDArray[np.float64]) -> NDArray[np.bool_]:
        """Return a water flag for each ``(east_m, north_m)`` input point."""


def _axis_values(lower_m: float, upper_m: float, spacing_m: float) -> NDArray[np.float64]:
    """Generate axis coordinates while including both requested bounds."""
    interval_count = floor((upper_m - lower_m) / spacing_m)
    values = lower_m + np.arange(interval_count + 1, dtype=np.float64) * spacing_m
    if isclose(float(values[-1]), upper_m, rel_tol=1e-12, abs_tol=1e-9):
        values[-1] = upper_m
    else:
        values = np.concatenate((values, np.asarray([upper_m], dtype=np.float64)))
    return values


def generate_candidates(
    bounds_en_m: tuple[float, float, float, float],
    grid_spacing_m: float,
    water_mask: WaterMaskProtocol | None = None,
) -> NDArray[np.float64]:
    """Return grid candidate positions as an ``(N, 2)`` east/north array.

    ``bounds_en_m`` is ``(east_min, east_max, north_min, north_max)``. Both
    edges of each axis are included, even when the interval is not divisible by
    ``grid_spacing_m``; the final interval on that axis may therefore be
    shorter than the requested spacing. Candidates are ordered with east
    varying fastest, then north. If ``water_mask`` is provided, only points for
    which its vectorized ``is_water_batch`` method returns true are retained.
    An empty result is valid when the bounds contain no water grid points.
    """
    if len(bounds_en_m) != 4:
        raise ValueError("bounds_en_m must contain east_min, east_max, north_min, north_max.")
    east_min, east_max, north_min, north_max = map(float, bounds_en_m)
    if not all(map(isfinite, (east_min, east_max, north_min, north_max))):
        raise ValueError("Search bounds must be finite.")
    if east_min >= east_max or north_min >= north_max:
        raise ValueError("Search bounds must have positive width and height.")
    if not isfinite(grid_spacing_m) or grid_spacing_m <= 0:
        raise ValueError("grid_spacing_m must be finite and positive.")

    east_values = _axis_values(east_min, east_max, float(grid_spacing_m))
    north_values = _axis_values(north_min, north_max, float(grid_spacing_m))
    east_grid, north_grid = np.meshgrid(east_values, north_values, indexing="xy")
    candidates = np.column_stack((east_grid.ravel(), north_grid.ravel()))

    if water_mask is None:
        return candidates

    water_flags = np.asarray(water_mask.is_water_batch(candidates), dtype=np.bool_)
    if water_flags.shape != (len(candidates),):
        raise ValueError("water_mask.is_water_batch must return one boolean per candidate.")
    return candidates[water_flags]


__all__ = ["WaterMaskProtocol", "generate_candidates"]
