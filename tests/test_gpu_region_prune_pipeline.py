"""End-to-end pruning keeps every cell consistent with positive marker evidence."""

import pytest
from shapely.geometry import box

from maritime.geometry.vector_map import VectorMap
from maritime.search.gpu.region_prune_pipeline import prune_map_regions_gpu
from maritime.search.gpu.region_pruning import MarkerDetection, RegionObject


def gpu_or_skip():
    cp = pytest.importorskip("cupy")
    try:
        if cp.cuda.runtime.getDeviceCount() < 1:
            pytest.skip("CUDA device unavailable")
    except Exception as exc:
        pytest.skip(f"CUDA runtime unavailable: {exc}")
    return cp


def run_pipeline(detections, objects, *, complete=True, max_evaluations=None):
    coverage = box(-2_000, -2_000, 22_000, 12_000)
    vector_map = VectorMap(None, crs="EPSG:32618", coverage_geometry=coverage)
    return prune_map_regions_gpu(
        vector_map,
        objects,
        detections,
        search_bounds_en_m=(0.0, 20_000.0, 0.0, 10_000.0),
        chart_coverage_geometry=coverage,
        chart_catalog_complete=complete,
        catalog_objects_cover_envelope=True,
        visibility_radius_m=1_000.0,
        certified_max_projection_scale=1.0,
        certified_azimuth_error_deg=0.0,
        minimum_distinct_markers=1 if detections else None,
        device_ids=(0,),
        initial_cell_size_m=10_000.0,
        target_cell_size_m=5_000.0,
        batch_size=2,
        max_evaluations=max_evaluations,
        cupy_module=gpu_or_skip(),
    )


def test_positive_marker_range_prunes_region_but_retains_possible_children():
    marker = RegionObject("aid", 15_000.0, 5_000.0, 0.0, identity_proven=True)
    result = run_pipeline((MarkerDetection(90.0, 10.0),), (marker,))
    assert result.complete
    assert result.pruned_count >= 1
    assert result.retained
    assert all(r.cell.bounds_en_m[0] >= 10_000 for r in result.retained)


def test_empty_observation_has_no_missed_marker_penalty():
    markers = (RegionObject("unobserved", 15_000.0, 5_000.0, 0.0, identity_proven=True),)
    result = run_pipeline((), markers)
    assert result.complete
    assert result.pruned_count == 0
    assert len(result.retained) == 8


def test_unknown_chart_coverage_remains_unresolved():
    result = run_pipeline((MarkerDetection(90.0, 10.0),), (), complete=False)
    assert result.complete
    assert result.pruned_count == 0
    assert len(result.retained) == 8
    assert all(r.decision.status == "unresolved" for r in result.retained)


def test_limit_reports_unprocessed_frontier():
    result = run_pipeline((), (), max_evaluations=1)
    assert not result.complete
    assert result.unprocessed
    assert result.pruned_count == 0
