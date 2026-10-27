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


def _evidence(**overrides):
    values = {
        "chart_coverage_complete": True,
        "land_fully_covers": False,
        "map_evidence_covers_visibility_envelope": True,
        "projection_reliable": True,
        "numeric_uncertainty": False,
    }
    values.update(overrides)
    return RegionEvidence(**values)


def _classify(cell, detections, evidence=None, **kwargs):
    return classify_region(
        cell,
        detections,
        evidence or _evidence(),
        visibility_radius_m=kwargs.pop("visibility_radius_m", 10_000.0),
        **kwargs,
    )


def test_visibility_envelope_uses_cell_bounds_plus_full_radius_not_center_radius():
    cell = RegionCell((0.0, 20_000.0, 0.0, 20_000.0))
    assert cell.covering_radius_m == pytest.approx(20_000.0 * 2**0.5 / 2)
    assert cell.visibility_envelope(10_000.0) == (-10_000.0, 30_000.0, -10_000.0, 30_000.0)
    # A marker at the far corner is more than 10 km from the cell center but
    # remains within the permitted object-search envelope for a 10 km radius.
    corner = RegionObject("corner", 30_000.0, 30_000.0, identity_proven=True)
    assert corner.east_m <= cell.bounds_en_m[1] + 10_000.0
    assert corner.north_m <= cell.bounds_en_m[3] + 10_000.0


def test_unknown_or_incomplete_chart_coverage_never_prunes():
    cell = RegionCell((0.0, 100.0, 0.0, 100.0))
    detections = (MarkerDetection(90.0, 2.0), MarkerDetection(180.0, 2.0))
    for coverage in (None, False):
        result = _classify(cell, detections, _evidence(chart_coverage_complete=coverage))
        assert result.status == "unresolved"


def test_only_known_land_covering_the_whole_observer_cell_can_prune():
    cell = RegionCell((0.0, 100.0, 0.0, 100.0))
    detections = (MarkerDetection(90.0, 3.0),)
    assert _classify(cell, detections, _evidence(land_fully_covers=True)).status == "prune"
    for state in (False, None):
        result = _classify(cell, detections, _evidence(land_fully_covers=state))
        assert result.status != "prune"


def test_no_positive_detections_cannot_penalize_missing_markers():
    result = _classify(RegionCell((0.0, 100.0, 0.0, 100.0)), ())
    assert result.status == "survive"


def test_missing_map_envelope_evidence_and_uncertainty_are_unresolved():
    cell = RegionCell((0.0, 100.0, 0.0, 100.0))
    detections = (MarkerDetection(0.0, 1.0),)
    for evidence in (
        _evidence(map_evidence_covers_visibility_envelope=False),
        _evidence(projection_reliable=False),
        _evidence(numeric_uncertainty=True),
    ):
        assert _classify(cell, detections, evidence).status == "unresolved"


def test_zero_and_360_wraparound_detection_is_retained_without_false_pruning():
    cell = RegionCell((-100.0, 100.0, -100.0, 100.0))
    objects = (
        RegionObject("east_aid", 20_000.0, 0.0, identity_proven=True),
        RegionObject("north_aid", 0.0, 20_000.0, identity_proven=True),
    )
    evidence = _evidence(objects=objects, certified_max_projection_scale=1.0)
    for bearing in (0.0, 359.999999, 360.0):
        detection = MarkerDetection(bearing, 1.0)
        result = _classify(cell, (detection,), evidence, certified_azimuth_error_deg=0.2)
        assert result.status == "survive"


def test_cell_containing_marker_point_keeps_all_bearings_feasible():
    cell = RegionCell((0.0, 100.0, 0.0, 100.0))
    evidence = _evidence(
        objects=(RegionObject("inside", 50.0, 50.0, identity_proven=True),),
        certified_max_projection_scale=1.0,
    )
    detections = (MarkerDetection(0.0, 0.1), MarkerDetection(180.0, 0.1))
    result = _classify(cell, detections, evidence, certified_azimuth_error_deg=0.0)
    assert result.status == "survive"
    assert result.compatible_object_masks == ((True,), (True,))


def test_duplicate_or_unproven_marker_identities_cannot_establish_a_low_count():
    cell = RegionCell((0.0, 100.0, 0.0, 100.0))
    detections = (MarkerDetection(15.0, 0.5), MarkerDetection(195.0, 0.5))
    duplicate_key = _evidence(
        objects=(
            RegionObject("same", 20_000.0, 0.0, identity_proven=True),
            RegionObject("same", -20_000.0, 0.0, identity_proven=True),
        )
    )
    unproven = _evidence(
        objects=(RegionObject("maybe_same", 20_000.0, 0.0, identity_proven=False),)
    )
    assert _classify(cell, detections, duplicate_key, minimum_distinct_markers=2).status != "prune"
    assert _classify(cell, detections, unproven, minimum_distinct_markers=2).status != "prune"


def test_region_crossing_and_cell_corner_object_search_are_not_pruned_by_center_distance():
    cell = RegionCell((0.0, 20_000.0, 0.0, 20_000.0))
    detections = (MarkerDetection(45.0, 2.0), MarkerDetection(225.0, 2.0))
    # At a cell corner, a valid observer can be over 10 km from the center;
    # the marker lies another 10 km out from the cell boundary.
    corner_marker = RegionObject("corner", 30_000.0, 30_000.0, identity_proven=True)
    result = _classify(
        cell,
        detections,
        _evidence(objects=(corner_marker,), certified_max_projection_scale=1.0),
        certified_azimuth_error_deg=1.0,
    )
    assert result.status == "survive"


def test_vessel_observation_without_ais_does_not_enable_region_pruning():
    cell = RegionCell((0.0, 100.0, 0.0, 100.0))
    vessel_bearing = MarkerDetection(45.0, 1.0)
    result = _classify(cell, (vessel_bearing,), _evidence(objects=()))
    assert result.status != "prune"


def test_positive_bearing_compatibility_masks_are_conservative_for_overlap_and_uncertainty():
    cell = RegionCell((0.0, 100.0, 0.0, 100.0))
    evidence = _evidence(
        objects=(
            RegionObject("overlap_a", 5_000.0, 0.0, 6_000.0, identity_proven=False),
            RegionObject("overlap_b", -5_001.0, 0.0, 6_000.0, identity_proven=False),
        ),
        certified_max_projection_scale=1.0,
    )
    result = _classify(
        cell,
        (MarkerDetection(90.0, 0.2), MarkerDetection(270.0, 0.2)),
        evidence,
        certified_azimuth_error_deg=0.5,
    )
    assert result.status == "survive"
    assert result.compatible_object_masks == ((True, True), (True, True))


def test_float_boundary_ambiguity_is_unresolved_instead_of_false_prune():
    cell = RegionCell((0.0, 1.0, 0.0, 1.0))
    evidence = _evidence(numeric_uncertainty=True)
    result = _classify(cell, (MarkerDetection(90.0, 1e-12),), evidence)
    assert result.status == "unresolved"


def test_cupy_batch_matches_cpu_or_skips_cleanly_without_cuda():
    try:
        import cupy as cp

        if cp.cuda.runtime.getDeviceCount() < 1:
            pytest.skip("CUDA device unavailable")
    except Exception as exc:  # pragma: no cover - depends on host CUDA setup
        pytest.skip(f"CuPy CUDA runtime unavailable: {exc}")

    cells = (RegionCell((0.0, 10.0, 0.0, 10.0)), RegionCell((20.0, 30.0, 20.0, 30.0)))
    evidence = (_evidence(), _evidence(chart_coverage_complete=None))
    detections = (MarkerDetection(0.0, 1.0),)
    expected = tuple(
        _classify(cell, detections, item) for cell, item in zip(cells, evidence, strict=True)
    )
    actual = classify_regions_cupy(
        cells, detections, evidence, visibility_radius_m=10_000.0, cupy_module=cp
    )
    assert tuple(item.status for item in actual) == tuple(item.status for item in expected)


def test_unproven_catalog_records_cannot_be_discarded_from_distinct_marker_upper_bound():
    cell = RegionCell((0.0, 100.0, 0.0, 100.0))
    detections = tuple(MarkerDetection(float(angle), 1.0) for angle in (0, 90, 180, 270))
    four_unproven = _evidence(
        objects=tuple(
            RegionObject(f"uncertain_{index}", float(index), 20_000.0, identity_proven=False)
            for index in range(4)
        )
    )
    three_proven_one_unknown = _evidence(
        objects=(
            RegionObject("p1", 0.0, 20_000.0, identity_proven=True),
            RegionObject("p2", 1.0, 20_000.0, identity_proven=True),
            RegionObject("p3", 2.0, 20_000.0, identity_proven=True),
            RegionObject("unknown", 3.0, 20_000.0, identity_proven=False),
        )
    )
    for evidence in (four_unproven, three_proven_one_unknown):
        result = _classify(cell, detections, evidence, minimum_distinct_markers=4)
        assert result.status != "prune"


def test_projected_range_uses_certified_scale_to_retain_geodesically_visible_aid():
    cell = RegionCell((0.0, 1.0, 0.0, 1.0))
    detection = (MarkerDetection(90.0, 1.0),)
    aid = RegionObject("scale_edge", 10_100.0, 0.5, 0.0, identity_proven=True)
    no_scale = _evidence(objects=(aid,))
    scaled = _evidence(objects=(aid,), certified_max_projection_scale=1.02)

    # With no scale certificate, distance cannot rule this marker out.
    assert (
        _classify(
            cell,
            detection,
            no_scale,
            minimum_distinct_markers=1,
            certified_azimuth_error_deg=1.0,
        ).status
        == "unresolved"
    )
    # 10.1 km projected distance can still represent a <=10 km geodesic range
    # when the certified worst-case scale factor is 1.02.
    assert (
        _classify(
            cell,
            detection,
            scaled,
            minimum_distinct_markers=1,
            certified_azimuth_error_deg=1.0,
        ).status
        == "survive"
    )


def test_projection_scale_and_exact_position_certificates_gate_distance_pruning():
    cell = RegionCell((0.0, 1.0, 0.0, 1.0))
    detections = (MarkerDetection(0.0, 1.0),)
    uncertain_location = RegionObject("uncertain", 20_000.0, 0.5, None, identity_proven=True)
    no_scale = _evidence(objects=(uncertain_location,), certified_max_projection_scale=None)
    assert (
        _classify(
            cell,
            detections,
            no_scale,
            minimum_distinct_markers=1,
            certified_azimuth_error_deg=1.0,
        ).status
        == "unresolved"
    )

    # Unknown coordinate uncertainty is unbounded unless exactness is explicit.
    uncertain_with_scale = _evidence(
        objects=(uncertain_location,), certified_max_projection_scale=1.0
    )
    assert (
        _classify(
            cell,
            detections,
            uncertain_with_scale,
            minimum_distinct_markers=1,
            certified_azimuth_error_deg=1.0,
        ).status
        == "survive"
    )

    exact_location = RegionObject(
        "exact",
        20_000.0,
        0.5,
        None,
        identity_proven=True,
        exact_position_asserted=True,
    )
    exact_with_scale = _evidence(objects=(exact_location,), certified_max_projection_scale=1.0)
    assert (
        _classify(
            cell,
            detections,
            exact_with_scale,
            minimum_distinct_markers=1,
            certified_azimuth_error_deg=1.0,
        ).status
        == "prune"
    )


def test_minimum_marker_count_cannot_exceed_positive_detection_count():
    cell = RegionCell((0.0, 1.0, 0.0, 1.0))
    with pytest.raises(ValueError, match="cannot exceed"):
        _classify(
            cell,
            (MarkerDetection(90.0, 1.0),),
            _evidence(),
            minimum_distinct_markers=2,
        )


def test_cupy_scale_and_unknown_uncertainty_counts_match_conservative_cpu_results():
    try:
        import cupy as cp

        if cp.cuda.runtime.getDeviceCount() < 1:
            pytest.skip("CUDA device unavailable")
    except Exception as exc:  # pragma: no cover - depends on host CUDA setup
        pytest.skip(f"CuPy CUDA runtime unavailable: {exc}")

    cells = (
        RegionCell((0.0, 1.0, 0.0, 1.0)),
        RegionCell((0.0, 1.0, 0.0, 1.0)),
        RegionCell((0.0, 1.0, 0.0, 1.0)),
    )
    evidence = (
        _evidence(
            objects=(RegionObject("outside", 20_000.0, 0.5, 0.0, True, True),),
            certified_max_projection_scale=1.0,
        ),
        _evidence(
            objects=(RegionObject("scale_edge", 10_100.0, 0.5, 0.0, True, True),),
            certified_max_projection_scale=1.02,
        ),
        _evidence(
            objects=(RegionObject("unknown_pos", 20_000.0, 0.5, None, True),),
            certified_max_projection_scale=1.0,
        ),
    )
    detections = (MarkerDetection(0.0, 1.0), MarkerDetection(180.0, 1.0))
    expected = tuple(
        _classify(
            cell,
            detections,
            item,
            minimum_distinct_markers=1,
        )
        for cell, item in zip(cells, evidence, strict=True)
    )
    actual = classify_regions_cupy(
        cells,
        detections,
        evidence,
        visibility_radius_m=10_000.0,
        minimum_distinct_markers=1,
        cupy_module=cp,
    )
    assert tuple(item.status for item in actual) == tuple(item.status for item in expected)
    assert actual[0].status == "prune"
    assert actual[1].status == "unresolved"
    assert actual[2].status == "unresolved"


def test_asymmetric_envelope_uses_xy_bound_order_and_exact_chart_scope():
    from shapely.geometry import box

    from maritime.geometry.vector_map import VectorMap
    from maritime.search.region_evidence import build_region_evidence

    # RegionCell stores (xmin, xmax, ymin, ymax), while Shapely box takes
    # (xmin, ymin, xmax, ymax). The distinct x/y values catch swapped axes.
    cell = RegionCell((100.0, 200.0, 400.0, 600.0))
    envelope = box(89.99, 389.99, 210.01, 610.01)
    land = box(99.0, 399.0, 201.0, 601.0)
    vector_map = VectorMap(land, crs="EPSG:32618", coverage_geometry=envelope)
    complete = build_region_evidence(
        vector_map,
        cell,
        visibility_radius_m=10.0,
        chart_coverage_geometry=envelope,
        chart_catalog_complete=True,
        catalog_objects_cover_envelope=True,
        certified_max_projection_scale=1.0,
    )
    assert complete.map_evidence_covers_visibility_envelope
    assert complete.land_fully_covers is True
    assert complete.chart_coverage_complete


def test_truncated_catalog_scope_is_never_marked_complete():
    from shapely.geometry import box

    from maritime.geometry.vector_map import VectorMap
    from maritime.search.region_evidence import build_region_evidence

    cell = RegionCell((100.0, 200.0, 400.0, 600.0))
    envelope = box(89.99, 389.99, 210.01, 610.01)
    vector_map = VectorMap(None, crs="EPSG:32618", coverage_geometry=envelope)
    truncated = build_region_evidence(
        vector_map,
        cell,
        visibility_radius_m=10.0,
        chart_coverage_geometry=envelope,
        chart_catalog_complete=True,
        catalog_objects_cover_envelope=False,
        certified_max_projection_scale=1.0,
    )
    assert truncated.chart_coverage_complete is False


def test_projection_scale_multiplies_position_uncertainty_for_range_and_angle():
    try:
        import cupy as cp

        if cp.cuda.runtime.getDeviceCount() < 1:
            pytest.skip("CUDA device unavailable")
    except Exception as exc:  # pragma: no cover - depends on host CUDA setup
        pytest.skip(f"CuPy CUDA runtime unavailable: {exc}")

    # At 11.105 km projected distance, the 100 m position uncertainty is
    # enlarged by 1.1, yielding a conservative 11.110 km visibility limit.
    cell = RegionCell((0.0, 0.01, 0.0, 0.01))
    detection = (MarkerDetection(0.55, 0.01),)
    evidence = _evidence(
        objects=(RegionObject("uncertain_aid", 0.005, 11_105.0, 100.0, True),),
        certified_max_projection_scale=1.1,
    )
    result = classify_regions_cupy(
        (cell,),
        detection,
        (evidence,),
        visibility_radius_m=10_000.0,
        minimum_distinct_markers=1,
        certified_azimuth_error_deg=0.0,
        cupy_module=cp,
    )[0]
    assert result.status == "survive"
    assert result.compatible_object_masks == ((True,),)


def test_projection_scale_retains_the_requested_11080m_uncertain_object():
    cell = RegionCell((0.0, 1.0, 0.0, 1.0))
    detection = (MarkerDetection(0.0, 1.0),)
    evidence = _evidence(
        objects=(RegionObject("uncertain_aid", 0.5, 11_080.0, 100.0, True),),
        certified_max_projection_scale=1.1,
    )
    assert (
        _classify(
            cell,
            detection,
            evidence,
            minimum_distinct_markers=1,
            certified_azimuth_error_deg=1.0,
        ).status
        == "survive"
    )


@pytest.mark.parametrize("bad_radius", (-1.0, float("nan")))
def test_cpu_and_gpu_reject_negative_or_nan_visibility_radius(bad_radius):
    cell = RegionCell((0.0, 1.0, 0.0, 1.0))
    evidence = _evidence()
    detections = (MarkerDetection(0.0, 1.0),)
    with pytest.raises(ValueError, match="visibility radius"):
        classify_region(cell, detections, evidence, visibility_radius_m=bad_radius)
    try:
        import cupy as cp

        if cp.cuda.runtime.getDeviceCount() < 1:
            pytest.skip("CUDA device unavailable")
    except Exception as exc:  # pragma: no cover - depends on host CUDA setup
        pytest.skip(f"CuPy CUDA runtime unavailable: {exc}")
    with pytest.raises(ValueError, match="visibility radius"):
        classify_regions_cupy(
            (cell,), detections, (evidence,), visibility_radius_m=bad_radius, cupy_module=cp
        )


@pytest.mark.parametrize("bad_error", (-1.0, float("nan")))
def test_cpu_and_gpu_reject_negative_or_nan_azimuth_error(bad_error):
    cell = RegionCell((0.0, 1.0, 0.0, 1.0))
    evidence = _evidence()
    detections = (MarkerDetection(0.0, 1.0),)
    with pytest.raises(ValueError, match="azimuth error"):
        classify_region(
            cell,
            detections,
            evidence,
            visibility_radius_m=10_000.0,
            certified_azimuth_error_deg=bad_error,
        )
    try:
        import cupy as cp

        if cp.cuda.runtime.getDeviceCount() < 1:
            pytest.skip("CUDA device unavailable")
    except Exception as exc:  # pragma: no cover - depends on host CUDA setup
        pytest.skip(f"CuPy CUDA runtime unavailable: {exc}")
    with pytest.raises(ValueError, match="azimuth error"):
        classify_regions_cupy(
            (cell,),
            detections,
            (evidence,),
            visibility_radius_m=10_000.0,
            certified_azimuth_error_deg=bad_error,
            cupy_module=cp,
        )


def test_cupy_two_cell_two_detection_masks_match_scalar_for_per_cell_scales():
    try:
        import cupy as cp

        if cp.cuda.runtime.getDeviceCount() < 1:
            pytest.skip("CUDA device unavailable")
    except Exception as exc:  # pragma: no cover - depends on host CUDA setup
        pytest.skip(f"CuPy CUDA runtime unavailable: {exc}")

    cells = (
        RegionCell((0.0, 1.0, 0.0, 1.0)),
        RegionCell((20_000.0, 20_001.0, -1.0, 0.0)),
    )
    evidence = (
        _evidence(
            objects=(
                RegionObject("north_1", 0.5, 5_000.0, 10.0, True),
                RegionObject("east_1", 5_000.0, 0.5, 10.0, True),
                RegionObject("far_1", 14_000.0, 0.5, 0.0, True, True),
            ),
            certified_max_projection_scale=1.0,
        ),
        _evidence(
            objects=(
                RegionObject("north_2", 20_000.5, 11_000.0, 100.0, True),
                RegionObject("east_2", 31_000.0, -0.5, 100.0, True),
                RegionObject("far_2", 31_500.0, -0.5, 0.0, True, True),
            ),
            certified_max_projection_scale=1.2,
        ),
    )
    detections = (MarkerDetection(0.0, 0.2), MarkerDetection(90.0, 0.2))
    kwargs = {
        "minimum_distinct_markers": 1,
        "certified_azimuth_error_deg": 0.1,
    }
    expected = tuple(
        _classify(cell, detections, item, **kwargs)
        for cell, item in zip(cells, evidence, strict=True)
    )
    actual = classify_regions_cupy(
        cells,
        detections,
        evidence,
        visibility_radius_m=10_000.0,
        cupy_module=cp,
        **kwargs,
    )
    assert tuple((item.status, item.compatible_object_masks) for item in actual) == tuple(
        (item.status, item.compatible_object_masks) for item in expected
    )
    assert actual[0].status == actual[1].status == "survive"
    assert actual[0].compatible_object_masks != actual[1].compatible_object_masks
    assert actual[0].compatible_object_masks[0][2] is False
    assert actual[1].compatible_object_masks[0][2] is False
    assert actual[1].compatible_object_masks[1][2] is True


def test_empty_detections_with_catalog_objects_have_no_missing_marker_penalty_on_gpu():
    try:
        import cupy as cp

        if cp.cuda.runtime.getDeviceCount() < 1:
            pytest.skip("CUDA device unavailable")
    except Exception as exc:  # pragma: no cover - depends on host CUDA setup
        pytest.skip(f"CuPy CUDA runtime unavailable: {exc}")

    cell = RegionCell((0.0, 1.0, 0.0, 1.0))
    evidence = _evidence(
        objects=(
            RegionObject("catalog_a", 0.5, 100.0, identity_proven=True),
            RegionObject("catalog_b", 200.0, 0.5, identity_proven=True),
        ),
        certified_max_projection_scale=1.0,
    )
    cpu = _classify(cell, (), evidence, minimum_distinct_markers=None)
    gpu = classify_regions_cupy(
        (cell,), (), (evidence,), visibility_radius_m=10_000.0, cupy_module=cp
    )[0]
    assert cpu.status == gpu.status == "survive"
    assert cpu.compatible_object_masks == gpu.compatible_object_masks == ()
