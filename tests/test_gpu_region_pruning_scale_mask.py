from __future__ import annotations

import pytest

from maritime.search.gpu.region_pruning import (
    MarkerDetection,
    RegionCell,
    RegionEvidence,
    RegionObject,
    classify_region,
    classify_regions_cupy,
)


def test_mixed_per_region_scale_masks_align_with_detection_cell_axes():
    cp = pytest.importorskip("cupy")
    try:
        if cp.cuda.runtime.getDeviceCount() < 1:
            pytest.skip("CUDA device unavailable")
    except Exception as exc:
        pytest.skip(f"CUDA runtime unavailable: {exc}")

    cells = (
        RegionCell((0.0, 1.0, 0.0, 1.0)),
        RegionCell((20_000.0, 20_001.0, 0.0, 1.0)),
    )
    common = dict(
        chart_coverage_complete=True,
        land_fully_covers=False,
        map_evidence_covers_visibility_envelope=True,
        projection_reliable=True,
        numeric_uncertainty=False,
    )
    evidence = (
        RegionEvidence(
            **common,
            objects=(RegionObject("unknown-scale", 10_000.0, 0.5, 0.0, True, True),),
            certified_max_projection_scale=None,
        ),
        RegionEvidence(
            **common,
            objects=(RegionObject("outside", 40_000.0, 0.5, 0.0, True, True),),
            certified_max_projection_scale=1.0,
        ),
    )
    detections = (MarkerDetection(90.0, 1.0), MarkerDetection(270.0, 1.0))
    expected = tuple(
        classify_region(
            cell,
            detections,
            item,
            visibility_radius_m=1_000.0,
            minimum_distinct_markers=1,
            certified_azimuth_error_deg=0.0,
        )
        for cell, item in zip(cells, evidence, strict=True)
    )
    actual = classify_regions_cupy(
        cells,
        detections,
        evidence,
        visibility_radius_m=1_000.0,
        minimum_distinct_markers=1,
        certified_azimuth_error_deg=0.0,
        cupy_module=cp,
    )
    assert actual == expected
    assert [item.status for item in actual] == ["unresolved", "prune"]
