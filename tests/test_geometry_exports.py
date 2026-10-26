"""Public geometry exports refer to the canonical implementation objects."""

import maritime.geometry as geometry
from maritime.geometry import angular_grouping, raster_los, spatial_index, vector_los, water_mask


def test_public_geometry_exports_are_canonical() -> None:
    assert geometry.WaterMask is water_mask.WaterMask
    assert geometry.Point2D is spatial_index.Point2D
    assert geometry.LandPolygon is vector_los.LandPolygon
    assert geometry.SpatialIndex2D is spatial_index.SpatialIndex2D
    assert geometry.line_of_sight_clear is vector_los.line_of_sight_clear
    assert geometry.raster_line_of_sight is raster_los.raster_line_of_sight
    assert geometry.DetectedObject is angular_grouping.DetectedObject
    assert geometry.GroupedDetections is angular_grouping.GroupedDetections
    assert geometry.group_angular_detections is angular_grouping.group_angular_detections
