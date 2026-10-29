"""The simulator, CPU search and cached GPU view use the same physical aid."""

from dataclasses import replace

import numpy as np
import pytest
from pyproj import Geod, Transformer
from shapely.geometry import box

from maritime.catalog_identity import canonical_aid_key
from maritime.geometry.vector_map import VectorMap
from maritime.schemas.inputs_v2 import CatalogRecordV2, CatalogV2
from maritime.search.deterministic_marker_scoring import MarkerObservation, score_marker_candidates
from maritime.search.gpu.resident.aid_view import canonicalize_prepared_world
from maritime.search.gpu.resident.engine import ResidentConfig
from maritime.search.gpu.resident.optimized import OptimizedResidentDevice
from maritime.search.gpu.resident.preparation import prepare_world
from maritime.search.prediction import predict_visible_groups, prepare_prediction_context
from maritime.search.region_evidence import project_catalog_objects
from maritime.simulator.mismatch import (
    KnownWaterObserver,
    PhysicalObjectV2,
    PhysicalSceneV2,
    SensorConfigurationV2,
)
from maritime.simulator.runner import run_simulator_v2

ORIGIN = (-79.905, 32.77)
TO_XY = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True)


def fixture():
    lon, lat, _ = Geod(ellps="WGS84").fwd(*ORIGIN, 0.0, 1000.0)
    metadata = (
        ("position_uncertainty_m", 0.0),
        ("visible_feature_offset_east_m", 0.0),
        ("visible_feature_offset_north_m", 0.0),
    )
    rows = tuple(
        CatalogRecordV2(key, lat, lon, normalized_type=label, metadata=metadata)
        for key, label in (("beacon", "beacon"), ("light", "light"), ("daymark", "daymark"))
    )
    catalog = CatalogV2(rows, "2023-07-15T12:00:30Z", "test", "1", "shared-aids")
    x, y = TO_XY.transform(*ORIGIN)
    coverage = box(x - 30000, y - 30000, x + 30000, y + 30000)
    vm = VectorMap(None, crs="EPSG:32618", coverage_geometry=coverage)
    return catalog, vm, canonical_aid_key(lat, lon), (x, y)


def test_simulator_prediction_scoring_and_pruning_share_one_object():
    catalog, vm, key, xy = fixture()
    scene = PhysicalSceneV2(catalog)
    assert len(scene.catalog.records) == 1
    sensor = SensorConfigurationV2(
        bearing_sigma_deg=0, detection_probability=1, expected_clutter_reports=0
    )
    simulation = run_simulator_v2(scene, KnownWaterObserver(*ORIGIN), vm, sensor, seed=11)
    assert len(simulation.truth.objects) == 1
    assert len(simulation.measurements.measurements) == 1
    assert simulation.diagnostics.groups[0].representative_catalog_key == key
    context = prepare_prediction_context(
        catalog, observer_crs="EPSG:32618", visibility_radius_m=10000, vector_map=vm
    )
    predicted = predict_visible_groups(
        xy,
        catalog,
        observer_crs="EPSG:32618",
        visibility_radius_m=10000,
        vector_map=vm,
        prepared_context=context,
    )
    assert len(context.records) == 1
    assert predicted.groups[0].member_keys == (key,)
    score = score_marker_candidates(
        [MarkerObservation("one", 0.0, 0.5)],
        predicted,
        catalog,
        chart_complete=True,
        catalog_exact_positions_asserted=True,
        catalog_nominal_visible_offsets_asserted=True,
        catalog_object_identities_proven=True,
    )
    assert score.status == "consistent"
    assert score.matched_count == 1
    assert len(project_catalog_objects(catalog.records, target_crs="EPSG:32618")) == 1
    # A second positive marker cannot be explained by duplicate source rows.
    two = score_marker_candidates(
        [MarkerObservation("one", 0.0, 0.5), MarkerObservation("two", 0.0, 0.5)],
        predicted,
        catalog,
        chart_complete=True,
        catalog_exact_positions_asserted=True,
        catalog_nominal_visible_offsets_asserted=True,
        catalog_object_identities_proven=True,
    )
    assert two.status == "inconsistent"


def test_uncataloged_aid_components_merge_but_colocated_vessels_do_not():
    catalog, _, _, _ = fixture()
    row = catalog.records[0]
    scene = PhysicalSceneV2(
        replace(catalog, records=(row,)),
        (
            PhysicalObjectV2("extra-light", row.longitude_deg, row.latitude_deg, raw_type="light"),
            PhysicalObjectV2("vessel", row.longitude_deg, row.latitude_deg, raw_type="vessel"),
        ),
    )
    assert len(scene.catalog.records) == 1
    assert [x.object_id for x in scene.uncataloged_physical_objects] == ["vessel"]


def test_synthetic_gpu_view_removes_component_tie_without_relaxing_flags():
    cp = pytest.importorskip("cupy")
    if cp.cuda.runtime.getDeviceCount() <= 3:
        pytest.skip("GPU3 preserves the ongoing production run")
    catalog, vm, key, (x, y) = fixture()
    base = prepare_world(
        vm,
        catalog,
        chart_geometry=vm._coverage,
        catalog_complete=True,
        catalog_objects_cover_envelope=True,
        identities_certified=True,
        exact_positions_asserted=True,
        nominal_offsets_asserted=True,
        max_projection_scale=2,
        azimuth_error_deg=30,
    )
    old_rows = base.objects.copy()
    view = canonicalize_prepared_world(base, catalog)
    assert len(base.objects) == 3 and len(view.objects) == 1
    np.testing.assert_array_equal(base.objects, old_rows)
    cfg = ResidentConfig(
        (x - 1, x + 1, y - 1, y + 1),
        (3,),
        object_capacity=8,
        trusted_marker_labels=True,
        hard_bearing_bounds_asserted=True,
        numerical_validation_accepted=True,
    )
    gpu = OptimizedResidentDevice(view, cfg, 3)
    with cp.cuda.Device(3), gpu.stream:
        result = gpu.score(
            cp.asarray([[x - 0.1, y - 0.1, x + 0.1, y + 0.1]]), cp.asarray([0.0]), cp.asarray([0.5])
        )
        gpu.stream.synchronize()
        assert cp.asnumpy(result["flags"]).tolist() == [0]
        assert cp.asnumpy(result["status"]).tolist() == [0]
        assert view.object_keys[int(result["matched_records"][0, 0].item())] == key


def test_merged_offset_conflicts_cannot_be_certified_by_generic_pruning():
    catalog, _, _, _ = fixture()
    rows = list(catalog.records)
    metadata = dict(rows[1].metadata)
    metadata["visible_feature_offset_east_m"] = 2.0
    rows[1] = replace(rows[1], metadata=tuple(metadata.items()))
    result = project_catalog_objects(
        rows,
        target_crs="EPSG:32618",
        uncertainty_m=0,
        exact_position_asserted=True,
        identity_proven=True,
        nominal_visible_offsets_asserted=True,
    )
    assert len(result) == 1
    assert result[0].position_uncertainty_m is None
    assert not result[0].exact_position_asserted


def test_simulator_requires_supported_geometry_for_merged_offset_conflicts():
    catalog, vm, _, _ = fixture()
    rows = list(catalog.records)
    metadata = dict(rows[1].metadata)
    metadata["visible_feature_offset_east_m"] = 2.0
    rows[1] = replace(rows[1], metadata=tuple(metadata.items()))
    scene = PhysicalSceneV2(replace(catalog, records=tuple(rows)))
    assert len(scene.catalog.records) == 1
    with pytest.raises(ValueError, match="component offsets"):
        run_simulator_v2(scene, KnownWaterObserver(*ORIGIN), vm, SensorConfigurationV2())
