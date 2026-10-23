"""Backward-compatible re-exports of the shared vector visibility API."""

from maritime_localization_shared.geospatial.vector_los import (
    LandPolygon,
    Point2D,
    line_of_sight_clear,
)

__all__ = ["LandPolygon", "Point2D", "line_of_sight_clear"]
