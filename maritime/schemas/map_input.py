"""Water/land raster contract supplied to simulator and search programs."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class MapInput:
    """Local projected grid where ``True`` cells are navigable water.

    ``water_mask`` is row-major from south to north; each row is west to east.
    The lower-left corner denotes the southwest edge of cell ``(0, 0)``. A
    ``False`` cell is conservatively treated as blocked, whether it represents
    land or nodata. Coordinates outside the raster extent are also blocked.
    Coordinates use the same local metric east/north frame and projected CRS as
    AIS inputs and estimates.
    """

    crs: str
    west_m: float
    south_m: float
    cell_size_m: float
    water_mask: tuple[tuple[bool, ...], ...]
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"Unsupported map schema version {self.schema_version}.")
        if not self.crs.strip():
            raise ValueError("crs must identify the projected coordinate reference system.")
        if not isfinite(self.west_m) or not isfinite(self.south_m):
            raise ValueError("Map origin coordinates must be finite.")
        if not isfinite(self.cell_size_m) or self.cell_size_m <= 0:
            raise ValueError("cell_size_m must be finite and positive.")
        if not isinstance(self.water_mask, tuple) or not self.water_mask:
            raise ValueError("water_mask must be a nonempty tuple of rows.")
        width = len(self.water_mask[0])
        if width == 0 or any(len(row) != width for row in self.water_mask):
            raise ValueError("water_mask rows must have equal nonzero width.")
        if any(type(cell) is not bool for row in self.water_mask for cell in row):
            raise TypeError("water_mask cells must be bool values.")

    @property
    def width_cells(self) -> int:
        return len(self.water_mask[0])

    @property
    def height_cells(self) -> int:
        return len(self.water_mask)
