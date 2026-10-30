"""Tiny synthetic end-to-end checks for coastwise marker search orchestration."""

from __future__ import annotations

import json
import threading
from math import atan2, degrees, hypot
from pathlib import Path

import numpy as np
import pytest
from pyproj import Geod, Transformer
from shapely.geometry import box

from maritime.geometry.vector_map import VectorMap
from maritime.schemas.inputs_v2 import CatalogRecordV2, CatalogV2
from maritime.search.coastwise_marker_search import (
    _catalog_region_objects,
    run_coastwise_marker_search,
)
from maritime.search.deterministic_marker_scoring import MarkerObservation

_CRS = "EPSG:32618"
_GEOD = Geod(ellps="WGS84")
_GEO_TO_XY = Transformer.from_crs("EPSG:4326", _CRS, always_xy=True)
_XY_TO_GEO = Transformer.from_crs(_CRS, "EPSG:4326", always_xy=True)
_ORIGIN = (500_000.0, 4_100_000.0)


class _Device:
    def __init__(self, device_id: int, seen: set[int], lock: threading.Lock):
        self.device_id = device_id
        self.seen = seen
        self.lock = lock

    def __enter__(self):
        with self.lock:
            self.seen.add(self.device_id)

    def __exit__(self, *_args):
        return False


class _NumpyCuda:
    def __init__(self):
        self.seen: set[int] = set()
        self.lock = threading.Lock()

    def Device(self, device_id: int):
        return _Device(device_id, self.seen, self.lock)


class _NumpyCup:
    """Small NumPy-backed CuPy surface that records explicit device selection."""

    def __init__(self):
        self.cuda = _NumpyCuda()

    def __getattr__(self, name):
        if name == "asnumpy":
            return np.asarray
        return getattr(np, name)


def _catalog(*records: CatalogRecordV2) -> CatalogV2:
    return CatalogV2(tuple(records), "2026-10-26T12:00:00Z", "synthetic", "1", "coastwise-test")


def _aid_at_xy(key: str, east: float, north: float) -> CatalogRecordV2:
    lon, lat = _XY_TO_GEO.transform(east, north)
    return CatalogRecordV2(
        key,
        lat,
        lon,
        raw_type="physical_aid",
        normalized_type="physical_aid",
        metadata=(
            ("physical_entity_id", f"entity-{key}"),
            ("physical_linkage_status", "resolved"),
            ("identity_ambiguous", False),
            ("position_uncertainty_m", 0.0),
            ("visible_feature_offset_east_m", 0.0),
            ("visible_feature_offset_north_m", 0.0),
        ),
    )


def _run(
    tmp_path: Path,
    *,
    bounds=(0.0, 4.0, 0.0, 4.0),
    initial=4.0,
    target=1.0,
    catalog=None,
    markers=(),
    chart_complete=True,
    chart_geometry=None,
    vector_map=None,
    max_evaluations=None,
    devices=(3,),
    position_uncertainty=None,
    exact_positions=False,
    identities_proven=False,
    minimum_distinct=None,
    nominal_offsets_asserted=False,
    matching_backend="cpu",
    cupy_module=None,
):
    x0, x1, y0, y1 = bounds
    coverage = box(_ORIGIN[0] - 200, _ORIGIN[1] - 200, _ORIGIN[0] + 200, _ORIGIN[1] + 200)
    vm = vector_map or VectorMap(None, crs=_CRS, coverage_geometry=coverage)
    cp = _NumpyCup() if cupy_module is None else cupy_module
    output = tmp_path / "search.json"
    result = run_coastwise_marker_search(
        vm,
        catalog or _catalog(),
        markers,
        search_bounds_en_m=(
            _ORIGIN[0] + x0,
            _ORIGIN[0] + x1,
            _ORIGIN[1] + y0,
            _ORIGIN[1] + y1,
        ),
        chart_coverage_geometry=coverage if chart_geometry is None else chart_geometry,
        chart_catalog_complete=chart_complete,
        catalog_objects_cover_envelope=chart_complete,
        catalog_object_identities_proven=identities_proven,
        catalog_position_uncertainty_m=position_uncertainty,
        catalog_exact_positions_asserted=exact_positions,
        catalog_nominal_visible_offsets_asserted=nominal_offsets_asserted,
        minimum_distinct_markers=minimum_distinct,
        visibility_radius_m=100.0,
        certified_max_projection_scale=1.0,
        certified_azimuth_error_deg=0.0,
        device_ids=devices,
        matching_backend=matching_backend,
        output_path=output,
        initial_cell_size_m=initial,
        target_cell_size_m=target,
        batch_size=1,
        max_evaluations=max_evaluations,
        enable_land_blocking=False,
        cupy_module=cp,
    )
    return result, json.loads(output.read_text()), cp


def test_complete_tiny_grid_scores_every_terminal_cell_without_top_n(tmp_path):
    result, payload, cp = _run(tmp_path, target=1.0)

    assert result.complete
    assert len(result.traversal.retained) == 16
    assert len(result.center_scores) == 16
    assert len({item.cell.bounds_en_m for item in result.center_scores}) == 16
    assert all(
        item.sample_semantics.startswith("cell_center_only") for item in result.center_scores
    )
    assert len(payload["traversal"]["retained"]) == 16
    assert len(payload["center_scores"]) == 16
    assert result.traversal.device_evaluations == ((3, result.traversal.evaluated_count),)
    assert cp.cuda.seen == {3}
    assert "ais" not in run_coastwise_marker_search.__code__.co_varnames
    assert "truth" not in run_coastwise_marker_search.__code__.co_varnames


def test_budget_stop_serializes_the_entire_frontier_and_keeps_processed_cells(tmp_path):
    result, payload, _ = _run(
        tmp_path,
        bounds=(0.0, 8.0, 0.0, 4.0),
        initial=4.0,
        target=1.0,
        max_evaluations=1,
    )

    assert result.status == "budget_limited"
    assert not result.complete
    # The evaluated root creates frontier children and leaves the second root queued.
    assert result.traversal.unprocessed
    serialized = payload["traversal"]["unprocessed_frontier"]
    assert len(serialized) == len(result.traversal.unprocessed)
    assert {(tuple(row["bounds_en_m"]), row["level"]) for row in serialized} == {
        (cell.bounds_en_m, cell.level) for cell in result.traversal.unprocessed
    }
    assert payload["metadata"]["budget_frontier_count"] == len(serialized)


def test_unknown_chart_coverage_never_scores_a_positive_detection_as_impossible(tmp_path):
    result, _, _ = _run(
        tmp_path,
        target=4.0,
        markers=(MarkerObservation("m", 90.0, 1.0),),
        chart_complete=False,
        chart_geometry=None,
    )

    assert result.center_scores
    for item in result.center_scores:
        assert item.score.status == "unresolved"
        assert item.score.matches[0].outcome == "unresolved"


def test_center_land_sample_does_not_remove_a_partly_water_cell(tmp_path):
    x, y = _ORIGIN
    coverage = box(x - 200, y - 200, x + 200, y + 200)
    # The center is land, while every terminal cell still contains water points.
    land = box(x - 0.2, y - 0.2, x + 0.2, y + 0.2)
    vm = VectorMap(land, crs=_CRS, coverage_geometry=coverage)
    result, _, _ = _run(
        tmp_path,
        bounds=(-1.0, 1.0, -1.0, 1.0),
        initial=2.0,
        target=2.0,
        vector_map=vm,
        catalog=_catalog(_aid_at_xy("east-aid", x + 50.0, y)),
        markers=(MarkerObservation("m", 90.0, 1.0),),
    )

    assert len(result.traversal.retained) == 1
    sample = result.center_scores[0]
    assert sample.map_status == "land"
    assert sample.score.status == "invalid_candidate"
    assert sample.sample_semantics.startswith("cell_center_only")
    assert sample.cell.bounds_en_m == result.traversal.retained[0].cell.bounds_en_m


def test_center_marker_score_wraps_across_zero_degrees_and_unobserved_aids_cost_nothing(tmp_path):
    x, y = _ORIGIN
    aid = _aid_at_xy("north-aid", x, y + 50.0)
    vessel_lon, vessel_lat = _XY_TO_GEO.transform(x + 50.0, y)
    unobserved_vessel = CatalogRecordV2(
        "unobserved-vessel",
        vessel_lat,
        vessel_lon,
        raw_type="vessel",
        normalized_type="vessel",
    )
    unobserved_aid = _aid_at_xy("unobserved-aid", x - 50.0, y)
    result, _, _ = _run(
        tmp_path,
        bounds=(-1.0, 1.0, -1.0, 1.0),
        initial=2.0,
        target=2.0,
        catalog=_catalog(aid, unobserved_vessel, unobserved_aid),
        markers=(MarkerObservation("north", 359.8, 1.0),),
    )

    assert result.center_scores[0].score.status == "consistent"
    assert result.center_scores[0].score.matches[0].outcome == "matched"

    no_observations, _, _ = _run(
        tmp_path / "empty",
        bounds=(-1.0, 1.0, -1.0, 1.0),
        initial=2.0,
        target=2.0,
        catalog=_catalog(aid),
    )
    assert no_observations.center_scores[0].score.status == "consistent"
    assert no_observations.traversal.pruned_count == 0


def test_position_unknown_is_retained_but_explicit_position_certificate_can_prune(tmp_path):
    x, y = _ORIGIN
    lon, lat = _XY_TO_GEO.transform(x + 1_000.0, y)
    catalog = _catalog(
        CatalogRecordV2(
            "distant",
            lat,
            lon,
            raw_type="physical_aid",
            normalized_type="physical_aid",
        )
    )
    marker = (MarkerObservation("m", 90.0, 0.1),)
    defaults, _, _ = _run(
        tmp_path / "defaults",
        bounds=(-1.0, 1.0, -1.0, 1.0),
        initial=2.0,
        target=2.0,
        catalog=catalog,
        markers=marker,
    )
    certified, _, _ = _run(
        tmp_path / "certified",
        bounds=(-1.0, 1.0, -1.0, 1.0),
        initial=2.0,
        target=2.0,
        catalog=catalog,
        markers=marker,
        position_uncertainty=0.0,
        exact_positions=True,
        nominal_offsets_asserted=True,
    )

    assert defaults.traversal.pruned_count == 0
    assert len(defaults.traversal.retained) == 1
    assert certified.traversal.pruned_count == 1
    assert not certified.traversal.retained


def test_minimum_distinct_markers_is_never_inferred_from_detection_count(tmp_path):
    x, y = _ORIGIN
    catalog = _catalog(_aid_at_xy("one-aid", x, y + 50.0))
    markers = (
        MarkerObservation("north-a", 0.0, 1.0),
        MarkerObservation("north-b", 0.0, 1.0),
    )
    default, _, _ = _run(
        tmp_path / "default-minimum",
        bounds=(-1.0, 1.0, -1.0, 1.0),
        initial=2.0,
        target=2.0,
        catalog=catalog,
        markers=markers,
        position_uncertainty=0.0,
        exact_positions=True,
        identities_proven=True,
    )
    explicit, _, _ = _run(
        tmp_path / "explicit-minimum",
        bounds=(-1.0, 1.0, -1.0, 1.0),
        initial=2.0,
        target=2.0,
        catalog=catalog,
        markers=markers,
        position_uncertainty=0.0,
        exact_positions=True,
        identities_proven=True,
        minimum_distinct=2,
    )

    assert default.traversal.pruned_count == 0
    assert len(default.traversal.retained) == 1
    assert explicit.traversal.pruned_count == 1
    assert not explicit.traversal.retained


def test_center_score_error_preserves_cell_and_completion_is_not_resolution(tmp_path, monkeypatch):
    import maritime.search.coastwise_marker_search as search_module

    def fail_prediction(*_args, **_kwargs):
        raise RuntimeError("synthetic score failure")

    monkeypatch.setattr(search_module, "predict_visible_groups", fail_prediction)
    result, _, _ = _run(
        tmp_path,
        bounds=(-1.0, 1.0, -1.0, 1.0),
        initial=2.0,
        target=2.0,
        markers=(MarkerObservation("impossible", 90.0, 0.1),),
        catalog=_catalog(_aid_at_xy("east-aid", _ORIGIN[0] + 50.0, _ORIGIN[1])),
    )

    assert result.complete
    assert len(result.traversal.retained) == 1
    assert not result.center_scores
    assert result.score_errors[0][0] == result.traversal.retained[0].cell.bounds_en_m
    assert "synthetic score failure" in result.score_errors[0][1]


def test_unresolved_marker_identity_is_not_a_hard_pruning_constraint(tmp_path):
    unresolved = MarkerObservation(
        "unresolved",
        90.0,
        0.1,
        positive=True,
        identity_status="unresolved",
        tolerance_calibrated=True,
    )
    result, _, _ = _run(
        tmp_path,
        bounds=(-1.0, 1.0, -1.0, 1.0),
        initial=2.0,
        target=2.0,
        catalog=_catalog(),
        markers=(unresolved,),
    )

    assert result.traversal.pruned_count == 0
    assert len(result.traversal.retained) == 1
    assert result.center_scores[0].score.status == "unresolved"


def test_pruning_catalog_filters_known_non_aids_but_preserves_unknown_rows():
    x, y = _ORIGIN
    lon, lat = _XY_TO_GEO.transform(x + 30.0, y + 20.0)
    catalog = _catalog(
        _aid_at_xy("physical-aid", x, y + 50.0),
        CatalogRecordV2("vessel", lat, lon, raw_type="vessel", normalized_type="vessel"),
        CatalogRecordV2(
            "virtual-aid", lat, lon, raw_type="virtual_aid", normalized_type="virtual_aid"
        ),
        CatalogRecordV2("unknown", lat, lon, raw_type="unmapped-type"),
    )
    coverage = box(x - 200, y - 200, x + 200, y + 200)
    vector_map = VectorMap(None, crs=_CRS, coverage_geometry=coverage)

    objects = _catalog_region_objects(
        catalog,
        vector_map,
        position_uncertainty_m=None,
        identity_proven=False,
        exact_position_asserted=False,
        nominal_visible_offsets_asserted=False,
    )

    assert {obj.key for obj in objects} == {"physical-aid", "unknown"}


def test_calibrated_visible_offsets_use_wgs84_forward_projection():
    reference_x, reference_y = _ORIGIN[0] + 1_200.0, _ORIGIN[1] + 35.0
    reference_lon, reference_lat = _XY_TO_GEO.transform(reference_x, reference_y)
    east_offset, north_offset = -1_150.0, -15.0
    record = CatalogRecordV2(
        "offset-aid",
        reference_lat,
        reference_lon,
        raw_type="physical_aid",
        normalized_type="physical_aid",
        metadata=(
            ("visible_feature_offset_east_m", east_offset),
            ("visible_feature_offset_north_m", north_offset),
        ),
    )
    catalog = _catalog(record)
    coverage = box(
        _ORIGIN[0] - 200,
        _ORIGIN[1] - 200,
        _ORIGIN[0] + 200,
        _ORIGIN[1] + 200,
    )
    vector_map = VectorMap(None, crs=_CRS, coverage_geometry=coverage)

    obj = _catalog_region_objects(
        catalog,
        vector_map,
        position_uncertainty_m=0.0,
        identity_proven=True,
        exact_position_asserted=True,
    )[0]
    offset_bearing = degrees(atan2(east_offset, north_offset))
    offset_range = hypot(east_offset, north_offset)
    expected_lon, expected_lat, _ = _GEOD.fwd(
        reference_lon, reference_lat, offset_bearing, offset_range
    )
    expected_east, expected_north = _GEO_TO_XY.transform(expected_lon, expected_lat)

    assert obj.east_m == pytest.approx(expected_east, abs=1e-7)
    assert obj.north_m == pytest.approx(expected_north, abs=1e-7)
    assert obj.position_uncertainty_m == 0.0
    assert obj.exact_position_asserted


def test_unknown_and_unsupported_offsets_do_not_inherit_position_certificates():
    x, y = _ORIGIN
    lon, lat = _XY_TO_GEO.transform(x + 1_000.0, y)
    catalog = _catalog(
        CatalogRecordV2("nominal", lat, lon, normalized_type="physical_aid"),
        CatalogRecordV2(
            "unsupported",
            lat,
            lon + 0.001,
            normalized_type="physical_aid",
            metadata=(("visible_feature_offset_east_m", 500.0),),
        ),
    )
    coverage = box(x - 200, y - 200, x + 200, y + 200)
    vector_map = VectorMap(None, crs=_CRS, coverage_geometry=coverage)

    unknown_by_default, unsupported = _catalog_region_objects(
        catalog,
        vector_map,
        position_uncertainty_m=0.0,
        identity_proven=True,
        exact_position_asserted=True,
        nominal_visible_offsets_asserted=False,
    )
    nominal_asserted, unsupported_asserted = _catalog_region_objects(
        catalog,
        vector_map,
        position_uncertainty_m=0.0,
        identity_proven=True,
        exact_position_asserted=True,
        nominal_visible_offsets_asserted=True,
    )

    assert unknown_by_default.position_uncertainty_m is None
    assert not unknown_by_default.exact_position_asserted
    assert nominal_asserted.position_uncertainty_m == 0.0
    assert nominal_asserted.exact_position_asserted
    assert unsupported.position_uncertainty_m is None
    assert not unsupported.exact_position_asserted
    assert unsupported_asserted.position_uncertainty_m is None
    assert not unsupported_asserted.exact_position_asserted


def test_calibrated_offset_keeps_shifted_aid_from_being_false_pruned(tmp_path):
    x, y = _ORIGIN
    reference_lon, reference_lat = _XY_TO_GEO.transform(x + 1_050.0, y)
    shifted_aid = CatalogRecordV2(
        "shifted-east-aid",
        reference_lat,
        reference_lon,
        raw_type="physical_aid",
        normalized_type="physical_aid",
        metadata=(
            ("physical_entity_id", "shifted-east-aid"),
            ("physical_linkage_status", "resolved"),
            ("identity_ambiguous", False),
            ("position_uncertainty_m", 0.0),
            ("visible_feature_offset_east_m", -1_000.0),
            ("visible_feature_offset_north_m", 0.0),
        ),
    )

    result, _, _ = _run(
        tmp_path,
        bounds=(-1.0, 1.0, -1.0, 1.0),
        initial=2.0,
        target=2.0,
        catalog=_catalog(shifted_aid),
        markers=(MarkerObservation("east", 90.0, 2.0),),
        position_uncertainty=0.0,
        exact_positions=True,
    )

    assert result.traversal.pruned_count == 0
    assert len(result.traversal.retained) == 1


def test_per_record_uncertainty_overrides_tighter_global_exactness_at_range_and_angle_edges(
    tmp_path,
):
    x, y = _ORIGIN
    lon, lat = _XY_TO_GEO.transform(x + 190.0, y)
    catalog = _catalog(
        CatalogRecordV2(
            "wide-uncertainty-aid",
            lat,
            lon,
            raw_type="physical_aid",
            normalized_type="physical_aid",
            metadata=(
                ("physical_entity_id", "wide-uncertainty-aid"),
                ("physical_linkage_status", "resolved"),
                ("identity_ambiguous", False),
                ("position_uncertainty_m", 100.0),
                ("visible_feature_offset_east_m", 0.0),
                ("visible_feature_offset_north_m", 0.0),
            ),
        )
    )
    coverage = box(x - 200, y - 200, x + 300, y + 300)
    vector_map = VectorMap(None, crs=_CRS, coverage_geometry=coverage)
    region_object = _catalog_region_objects(
        catalog,
        vector_map,
        position_uncertainty_m=5.0,
        identity_proven=True,
        exact_position_asserted=False,
        nominal_visible_offsets_asserted=False,
    )[0]
    assert region_object.position_uncertainty_m >= 100.0
    assert not region_object.exact_position_asserted
    with_exact_assertion = _catalog_region_objects(
        catalog,
        vector_map,
        position_uncertainty_m=None,
        identity_proven=True,
        exact_position_asserted=True,
        nominal_visible_offsets_asserted=False,
    )[0]
    assert with_exact_assertion.position_uncertainty_m >= 100.0
    assert not with_exact_assertion.exact_position_asserted

    result, _, _ = _run(
        tmp_path,
        bounds=(-1.0, 1.0, -1.0, 1.0),
        initial=2.0,
        target=2.0,
        catalog=catalog,
        vector_map=vector_map,
        markers=(MarkerObservation("edge", 120.0, 0.5),),
        position_uncertainty=5.0,
        exact_positions=False,
        identities_proven=True,
    )

    # At 190m range, a 100m bound admits a possible 90m range and a 30 degree
    # bearing shift. The narrower global bound cannot safely eliminate it.
    assert result.traversal.pruned_count == 0
    assert len(result.traversal.retained) == 1


def test_coastwise_gpu_final_matching_matches_cpu_score_on_synthetic_candidate(tmp_path):
    cp = pytest.importorskip("cupy")
    try:
        if cp.cuda.runtime.getDeviceCount() < 1:
            pytest.skip("CUDA device unavailable")
    except Exception as exc:
        pytest.skip(f"CUDA runtime unavailable: {exc}")

    from maritime.search.deterministic_marker_scoring import score_marker_candidates
    from maritime.search.prediction import predict_visible_groups

    x, y = _ORIGIN
    aid = _aid_at_xy("gpu-aid", x + 50.0, y)
    catalog = _catalog(aid)
    observer_lon, observer_lat = _XY_TO_GEO.transform(x, y)
    azimuth, _, _ = _GEOD.inv(observer_lon, observer_lat, aid.longitude_deg, aid.latitude_deg)
    markers = (MarkerObservation("east", azimuth % 360.0, 0.5),)
    coverage = box(x - 200, y - 200, x + 200, y + 200)
    vector_map = VectorMap(None, crs=_CRS, coverage_geometry=coverage)

    result, payload, _ = _run(
        tmp_path,
        bounds=(-1.0, 1.0, -1.0, 1.0),
        initial=2.0,
        target=2.0,
        catalog=catalog,
        markers=markers,
        vector_map=vector_map,
        devices=(0,),
        matching_backend="gpu",
        cupy_module=cp,
    )

    assert result.complete
    assert len(result.center_scores) == 1
    gpu_score = result.center_scores[0].score
    assert gpu_score.status == "consistent"
    assert gpu_score.matching_backend == "gpu"
    predicted = predict_visible_groups(
        result.center_scores[0].sample_position_xy_m,
        catalog,
        observer_crs=vector_map.crs,
        visibility_radius_m=100.0,
        vector_map=vector_map,
    )
    cpu_score = score_marker_candidates(
        markers,
        predicted,
        catalog,
        map_status=result.center_scores[0].map_status,
        chart_complete=result.center_scores[0].chart_complete,
    )
    assert gpu_score.status == cpu_score.status
    assert gpu_score.matches == cpu_score.matches
    assert gpu_score.association_ambiguous == cpu_score.association_ambiguous
    assert gpu_score.alternative_assignment == cpu_score.alternative_assignment
    assert gpu_score.total_abs_residual_deg == pytest.approx(
        cpu_score.total_abs_residual_deg, abs=1e-12
    )
    assert payload["metadata"]["matching_backend"] == "gpu"
    assert payload["center_scores"][0]["score"]["matching_backend"] == "gpu"
