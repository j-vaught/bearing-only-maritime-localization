"""Backward-compatible imports for the shared geospatial package."""

from maritime_localization_shared.geospatial import (
    LandPolygon,
    Point2D,
    SpatialIndex2D,
    WaterMask,
    line_of_sight_clear,
    raster_line_of_sight,
)

__all__ = [
    "LandPolygon",
    "Point2D",
    "SpatialIndex2D",
    "WaterMask",
    "line_of_sight_clear",
    "raster_line_of_sight",
]
