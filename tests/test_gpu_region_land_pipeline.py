"""Exercise land proof through the positive-only region traversal."""

import pytest
from shapely.geometry import box

from maritime.geometry.vector_map import VectorMap
from maritime.search.gpu.region_prune_pipeline import prune_map_regions_gpu
from maritime.search.gpu.region_pruning import MarkerDetection, RegionObject


def device():
    cp = pytest.importorskip("cupy")
    try:
        if cp.cuda.runtime.getDeviceCount() < 1:
            pytest.skip("CUDA unavailable")
    except Exception as error:
        pytest.skip(f"CUDA unavailable: {error}")
    return cp


def run(objects, detections, *, enabled=True):
    coverage = box(-200, -200, 200, 200)
    vector_map = VectorMap(box(20, 0, 30, 100), crs="EPSG:32618", coverage_geometry=coverage)
    return prune_map_regions_gpu(
        vector_map,
        objects,
        detections,
        search_bounds_en_m=(0.0, 10.0, 40.0, 50.0),
        chart_coverage_geometry=coverage,
        chart_catalog_complete=True,
        catalog_objects_cover_envelope=True,
        visibility_radius_m=100.0,
        certified_max_projection_scale=1.0,
        certified_azimuth_error_deg=0.0,
        minimum_distinct_markers=1 if detections else None,
        device_ids=(0,),
        initial_cell_size_m=10,
        target_cell_size_m=10,
        enable_land_blocking=enabled,
        cupy_module=device(),
    )


def test_region_is_rejected_only_when_all_compatible_markers_blocked():
    marker = RegionObject("marker", 50.0, 45.0, 0.0, True, True)
    observations = (MarkerDetection(90.0, 10.0),)
    without_land = run((marker,), observations, enabled=False)
    with_land = run((marker,), observations)
    assert len(without_land.retained) == 1 and without_land.pruned_count == 0
    assert with_land.complete and with_land.pruned_count == 1 and not with_land.retained
    assert any("land" in reason for reason, _ in with_land.prune_reasons)


def test_compatible_unblocked_alternative_prevents_rejection():
    blocked = RegionObject("blocked", 50.0, 45.0, 0.0, True, True)
    clear = RegionObject("clear", 5.0, 70.0, 0.0, True, True)
    result = run((blocked, clear), (MarkerDetection(90.0, 100.0),))
    assert result.complete and result.pruned_count == 0 and len(result.retained) == 1


def test_unobserved_markers_do_not_cause_land_rejection():
    marker = RegionObject("unobserved", 50.0, 45.0, 0.0, True, True)
    result = run((marker,), ())
    assert result.complete and result.pruned_count == 0 and len(result.retained) == 1


def test_uncertain_marker_position_does_not_prove_blockage():
    marker = RegionObject("uncertain", 50.0, 45.0, None, False, False)
    result = run((marker,), (MarkerDetection(90.0, 10.0),))
    assert result.complete and result.pruned_count == 0 and len(result.retained) == 1
