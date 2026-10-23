"""Public geospatial utilities shared by simulator and search programs.

The package exposes water classification, vector and raster line-of-sight
queries, and radius-based spatial indexing. Implementations remain available
from their defining modules for callers that need more specialized imports.
"""

from .raster_los import raster_line_of_sight
from .spatial_index import Point2D, SpatialIndex2D
from .vector_los import LandPolygon, line_of_sight_clear
from .water_mask import WaterMask

__all__ = [
    "LandPolygon",
    "Point2D",
    "SpatialIndex2D",
    "WaterMask",
    "line_of_sight_clear",
    "raster_line_of_sight",
]
