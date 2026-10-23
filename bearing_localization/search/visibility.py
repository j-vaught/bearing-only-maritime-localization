"""Candidate-specific range and line-of-sight filtering for search.

This module depends on structural query protocols rather than simulator or
geospatial implementations. Search can therefore apply the same map and
visibility rules through whichever shared-library adapters it is given.
Coordinates are projected east/north metres.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

import numpy as np
from numpy.typing import NDArray


class WaterQuery(Protocol):
    """Minimal water-mask interface required by candidate filtering."""

    def is_water(self, x_m: float, y_m: float) -> bool:
        """Return whether the projected point is on valid water."""


class LineOfSightQuery(Protocol):
    """Visibility query for one observer-to-target segment."""

    def is_clear(
        self, observer_xy_m: tuple[float, float], target_xy_m: tuple[float, float]
    ) -> bool:
        """Return whether land leaves the segment unobstructed."""


def filter_visible_candidates(
    observer_xy_m: Sequence[float],
    candidate_xy_m: NDArray[np.float64] | Sequence[Sequence[float]],
    *,
    visibility_radius_m: float = float("inf"),
    water_query: WaterQuery | None = None,
    line_of_sight_query: LineOfSightQuery | None = None,
) -> NDArray[np.int64]:
    """Return original candidate indices within range and visible from water.

    The range boundary is inclusive. If a water query is supplied, an observer
    on land or outside the mask yields no visible candidates. If a line-of-sight
    query is supplied, it is evaluated only for candidates inside range. The
    target itself is not required to be water, allowing shoreline aids and
    other fixed landmarks. Candidate order and duplicate locations are kept.

    A missing water or line-of-sight query disables that check. This permits
    callers to use the adapter with partial map data while keeping the search
    algorithm independent from the simulator and geospatial implementation.
    """
    observer = np.asarray(observer_xy_m, dtype=np.float64)
    if observer.shape != (2,) or not np.isfinite(observer).all():
        raise ValueError("observer_xy_m must contain two finite coordinates.")
    if np.isnan(visibility_radius_m) or visibility_radius_m <= 0:
        raise ValueError("visibility_radius_m must be positive and not NaN.")

    candidates = np.asarray(candidate_xy_m, dtype=np.float64)
    if candidates.ndim != 2 or candidates.shape[1] != 2:
        raise ValueError("candidate_xy_m must have shape (N, 2).")
    if not np.isfinite(candidates).all():
        raise ValueError("candidate coordinates must be finite.")

    if water_query is not None and not water_query.is_water(float(observer[0]), float(observer[1])):
        return np.empty(0, dtype=np.int64)

    offsets = candidates - observer[None, :]
    within_range = np.einsum("ij,ij->i", offsets, offsets) <= visibility_radius_m**2
    candidate_indices = np.flatnonzero(within_range)
    if line_of_sight_query is None or not len(candidate_indices):
        return candidate_indices.astype(np.int64, copy=False)

    observer_point = (float(observer[0]), float(observer[1]))
    visible_indices = [
        int(index)
        for index in candidate_indices
        if line_of_sight_query.is_clear(
            observer_point,
            (float(candidates[index, 0]), float(candidates[index, 1])),
        )
    ]
    return np.asarray(visible_indices, dtype=np.int64)


__all__ = ["LineOfSightQuery", "WaterQuery", "filter_visible_candidates"]
