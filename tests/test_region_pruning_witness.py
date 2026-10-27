"""A region containing a known compatible marker witness must never be pruned."""

import numpy as np
import pytest

from maritime.search.gpu.region_pruning import (
    MarkerDetection,
    RegionCell,
    RegionEvidence,
    RegionObject,
    classify_region,
    classify_regions_cupy,
)


def test_random_synthetic_witnesses_are_never_pruned_on_gpu():
    cp = pytest.importorskip("cupy")
    try:
        if cp.cuda.runtime.getDeviceCount() < 1:
            pytest.skip("CUDA device unavailable")
    except Exception as exc:
        pytest.skip(f"CUDA runtime unavailable: {exc}")
    rng = np.random.default_rng(7)
    detections = (MarkerDetection(30.0, 0.01), MarkerDetection(220.0, 0.01))
    cells = []
    evidence = []
    for i in range(100):
        x, y = rng.uniform(-100_000, 100_000, size=2)
        width, height = rng.uniform(100, 10_000, size=2)
        cell = RegionCell((x, x + width, y, y + height))
        observer = (rng.uniform(x, x + width), rng.uniform(y, y + height))
        objects = []
        for j, detection in enumerate(detections):
            angle = np.radians(detection.bearing_deg)
            distance = rng.uniform(1, 9_999)
            objects.append(
                RegionObject(
                    f"{i}-{j}",
                    observer[0] + distance * np.sin(angle),
                    observer[1] + distance * np.cos(angle),
                    0.0,
                    True,
                )
            )
        cells.append(cell)
        evidence.append(RegionEvidence(True, False, True, True, False, tuple(objects), 1.0))
    kwargs = dict(
        visibility_radius_m=10_000, minimum_distinct_markers=2, certified_azimuth_error_deg=0.0
    )
    gpu = classify_regions_cupy(cells, detections, evidence, cupy_module=cp, device_id=0, **kwargs)
    cpu = tuple(
        classify_region(cell, detections, ev, **kwargs)
        for cell, ev in zip(cells, evidence, strict=True)
    )
    assert all(d.status == "survive" for d in gpu)
    assert [d.status for d in gpu] == [d.status for d in cpu]
    assert [d.compatible_object_masks for d in gpu] == [d.compatible_object_masks for d in cpu]


def test_gpu_without_scale_certificate_does_not_prune_on_object_count():
    cp = pytest.importorskip("cupy")
    try:
        if cp.cuda.runtime.getDeviceCount() < 1:
            pytest.skip("CUDA device unavailable")
    except Exception as exc:
        pytest.skip(f"CUDA runtime unavailable: {exc}")
    cell = RegionCell((0.0, 100.0, 0.0, 100.0))
    marker = RegionObject("one", 50.0, 1_000.0, 0.0, True)
    ev = RegionEvidence(True, False, True, True, False, (marker,), None)
    reports = (MarkerDetection(0.0, 1.0), MarkerDetection(90.0, 1.0))
    result = classify_regions_cupy(
        [cell],
        reports,
        [ev],
        visibility_radius_m=10_000.0,
        minimum_distinct_markers=2,
        certified_azimuth_error_deg=0.0,
        cupy_module=cp,
        device_id=0,
    )
    assert result[0].status == "unresolved"
