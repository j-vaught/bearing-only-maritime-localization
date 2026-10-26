"""Shared coordinate-independent geospatial queries for all programs."""

from .angular_grouping import DetectedObject, GroupedDetections, group_angular_detections
from .raster_los import raster_line_of_sight
from .spatial_index import Point2D, SpatialIndex2D
from .vector_los import LandPolygon, line_of_sight_clear
from .water_mask import WaterMask

__all__ = [
    "DetectedObject",
    "GroupedDetections",
    "group_angular_detections",
    "LandPolygon",
    "Point2D",
    "SpatialIndex2D",
    "WaterMask",
    "line_of_sight_clear",
    "raster_line_of_sight",
]
