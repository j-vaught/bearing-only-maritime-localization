"""Water-constrained observer placement for simulated trials."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Protocol

import numpy as np

from bearing_localization.scene import ShipPose


class WaterGridProtocol(Protocol):
    """Projected grid geometry shared by map contracts and query masks."""

    @property
    def west_m(self) -> float: ...

    @property
    def south_m(self) -> float: ...

    @property
    def cell_size_m(self) -> float: ...


class MapInputProtocol(WaterGridProtocol, Protocol):
    """Shared map contract with a row-major water raster."""

    @property
    def water_mask(self) -> tuple[tuple[bool, ...], ...]: ...


class QueryWaterMaskProtocol(WaterGridProtocol, Protocol):
    """Geospatial query mask with water and optional source-validity rasters."""

    @property
    def water(self) -> np.ndarray: ...

    @property
    def valid(self) -> np.ndarray: ...

    @property
    def width_cells(self) -> int: ...

    @property
    def height_cells(self) -> int: ...


@dataclass(frozen=True, slots=True)
class ObserverPosition:
    """Observer position at the center of a valid water-mask cell."""

    east_m: float
    north_m: float
    row: int
    column: int


def to_ship_pose(position: ObserverPosition, *, heading_deg: float = 0.0) -> ShipPose:
    """Convert a sampled water-cell position to the runner's pose contract."""
    if not isinstance(position, ObserverPosition):
        raise TypeError("position must be an ObserverPosition.")
    if not isfinite(heading_deg):
        raise ValueError("heading_deg must be finite.")
    return ShipPose(position.east_m, position.north_m, heading_deg)


def sample_observers(
    water_mask: MapInputProtocol | QueryWaterMaskProtocol,
    count: int,
    rng: np.random.Generator,
    *,
    unique_cells: bool = True,
) -> tuple[ObserverPosition, ...]:
    """Sample observer positions from water cells using the supplied RNG.

    Positions are placed at cell centers, so a sampled point cannot fall on a
    neighboring land cell due to a grid-edge convention. Sampling is without
    replacement by default; set ``unique_cells=False`` to permit repeated
    positions when generating many trials from a small mask.
    """
    if count < 0:
        raise ValueError("count must be nonnegative.")
    if not isinstance(rng, np.random.Generator):
        raise TypeError("rng must be a numpy.random.Generator.")
    if count == 0:
        return ()

    mask_values = getattr(water_mask, "water_mask", None)
    if mask_values is None:
        mask_values = getattr(water_mask, "water", None)
    if mask_values is None:
        raise TypeError("water_mask must expose a row-major water_mask or water raster.")
    water = np.asarray(mask_values, dtype=bool)
    valid = getattr(water_mask, "valid", None)
    if valid is not None:
        water = water & np.asarray(valid, dtype=bool)
    rows, columns = np.nonzero(water)
    water_cell_count = len(rows)
    if water_cell_count == 0:
        raise ValueError("Cannot sample observers because the water mask has no water cells.")
    if unique_cells and count > water_cell_count:
        raise ValueError(
            f"Cannot sample {count} unique observer cells from {water_cell_count} water cells."
        )

    selected = rng.choice(water_cell_count, size=count, replace=not unique_cells)
    positions = []
    for index in np.atleast_1d(selected):
        row = int(rows[index])
        column = int(columns[index])
        positions.append(
            ObserverPosition(
                east_m=water_mask.west_m + (column + 0.5) * water_mask.cell_size_m,
                north_m=water_mask.south_m + (row + 0.5) * water_mask.cell_size_m,
                row=row,
                column=column,
            )
        )
    return tuple(positions)
