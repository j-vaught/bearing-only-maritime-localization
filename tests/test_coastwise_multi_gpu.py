"""Confirm queue dispatch on two actual devices without performance measurements."""

import pytest
from shapely.geometry import box

from maritime.geometry.vector_map import VectorMap
from maritime.search.gpu.region_prune_pipeline import prune_map_regions_gpu


def test_two_devices_process_regions_without_dropping_unconstrained_cells():
    cp = pytest.importorskip("cupy")
    try:
        if cp.cuda.runtime.getDeviceCount() < 2:
            pytest.skip("Two CUDA devices required")
    except Exception as error:
        pytest.skip(f"CUDA runtime unavailable: {error}")
    coverage = box(-100.0, -100.0, 300.0, 300.0)
    vm = VectorMap(None, crs="EPSG:32618", coverage_geometry=coverage)
    result = prune_map_regions_gpu(
        vm,
        (),
        (),
        search_bounds_en_m=(0.0, 200.0, 0.0, 200.0),
        chart_coverage_geometry=coverage,
        chart_catalog_complete=True,
        catalog_objects_cover_envelope=True,
        visibility_radius_m=10.0,
        certified_max_projection_scale=1.0,
        certified_azimuth_error_deg=0.0,
        minimum_distinct_markers=None,
        device_ids=(0, 1),
        initial_cell_size_m=100.0,
        target_cell_size_m=100.0,
        batch_size=1,
        cupy_module=cp,
    )
    assert result.complete
    assert result.pruned_count == 0
    assert len(result.retained) == 4
    assert all(count > 0 for _, count in result.device_evaluations)
