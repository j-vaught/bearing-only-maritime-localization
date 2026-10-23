"""Legacy package paths resolve to the shared implementation objects."""

import bearing_localization.geospatial as legacy
import maritime_localization_shared.geospatial as shared
from bearing_localization.geospatial import raster_los as legacy_raster
from bearing_localization.geospatial import spatial_index as legacy_spatial
from bearing_localization.geospatial import vector_los as legacy_vector
from bearing_localization.geospatial import water_mask as legacy_water
from maritime_localization_shared.geospatial import raster_los as shared_raster
from maritime_localization_shared.geospatial import spatial_index as shared_spatial
from maritime_localization_shared.geospatial import vector_los as shared_vector
from maritime_localization_shared.geospatial import water_mask as shared_water


def test_package_exports_are_identical_to_shared_implementation() -> None:
    assert legacy.WaterMask is shared.WaterMask
    assert legacy.Point2D is shared.Point2D
    assert legacy.LandPolygon is shared.LandPolygon
    assert legacy.SpatialIndex2D is shared.SpatialIndex2D
    assert legacy.line_of_sight_clear is shared.line_of_sight_clear
    assert legacy.raster_line_of_sight is shared.raster_line_of_sight


def test_legacy_module_paths_are_identical_to_shared_implementation() -> None:
    assert legacy_water.WaterMask is shared_water.WaterMask
    assert legacy_spatial.SpatialIndex2D is shared_spatial.SpatialIndex2D
    assert legacy_vector.line_of_sight_clear is shared_vector.line_of_sight_clear
    assert legacy_raster.raster_line_of_sight is shared_raster.raster_line_of_sight
