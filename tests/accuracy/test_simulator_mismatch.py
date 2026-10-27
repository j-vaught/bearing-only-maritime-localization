from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

import pytest
from pyproj import CRS, Geod, Transformer
from shapely.geometry import box

from maritime.geometry.vector_map import VectorMap
from maritime.schemas.inputs_v2 import CatalogRecordV2, CatalogV2
from maritime.simulator.mismatch import (
    KnownWaterObserver,
    PhysicalObjectV2,
    PhysicalSceneV2,
    SensorConfigurationV2,
    SimulatorMismatchConfig,
)
from maritime.simulator.runner import run_simulator_v2

ORIGIN = (-76.3, 37.0)
MAP_CRS = CRS.from_epsg(32618)
_TO_XY = Transformer.from_crs("EPSG:4326", MAP_CRS, always_xy=True)
_GEOD = Geod(ellps="WGS84")


def _at(bearing_deg: float, distance_m: float) -> tuple[float, float]:
    lon, lat, _ = _GEOD.fwd(ORIGIN[0], ORIGIN[1], bearing_deg, distance_m)
    return float(lon), float(lat)


def _record(key: str, bearing_deg: float, distance_m: float, label: str = "vessel", metadata=()):
    lon, lat = _at(bearing_deg, distance_m)
    return CatalogRecordV2(
        key=key,
        longitude_deg=lon,
        latitude_deg=lat,
        raw_type=label,
        normalized_type=label,
        metadata=tuple(metadata),
    )


def _catalog(*records: CatalogRecordV2) -> CatalogV2:
    return CatalogV2(
        records=tuple(records),
        timestamp_utc="2026-10-25T12:00:00Z",
        source="a15-fixture",
        catalog_version="1",
        catalog_id="a15",
    )


def _map(*, land=None) -> VectorMap:
    east, north = _TO_XY.transform(*ORIGIN)
    return VectorMap(
        land,
        crs=MAP_CRS,
        coverage_geometry=box(east - 20_000, north - 20_000, east + 20_000, north + 20_000),
    )


def _sensor(**kwargs) -> SensorConfigurationV2:
    values = {
        "visibility_radius_m": 10_000,
        "angular_resolution_deg": 0.1,
        "bearing_sigma_deg": 0.0,
        "detection_probability": 1.0,
        "type_error_probability": 0.0,
        "expected_clutter_reports": 0.0,
        "outlier_probability": 0.0,
        "report_type_labels": True,
    }
    values.update(kwargs)
    return SensorConfigurationV2(**values)


def _run(scene=None, *, sensor=None, mismatch=None, vector_map=None, seed=3):
    scene = scene or PhysicalSceneV2(_catalog(_record("one", 10.0, 1_000.0)))
    observer = KnownWaterObserver(*ORIGIN, heading_deg=123.0)
    return run_simulator_v2(
        scene,
        observer,
        vector_map or _map(),
        sensor or _sensor(),
        seed=seed,
        mismatch=mismatch,
        trial_id="a15",
    )


def test_default_sensor_fixture_records_requested_physical_defaults():
    fixture = json.loads((Path(__file__).parent / "fixtures" / "sensor_defaults.json").read_text())
    defaults = SensorConfigurationV2()
    for key, value in fixture.items():
        assert getattr(defaults, key) == value


def test_v2_outputs_are_deterministic_and_measurement_contract_has_no_truth():
    scene = PhysicalSceneV2(
        _catalog(_record("catalog", 10.0, 1_000.0)),
        (PhysicalObjectV2("uncataloged", *_at(45.0, 2_000.0), raw_type="buoy"),),
    )
    first = _run(scene, sensor=_sensor(bearing_sigma_deg=0.5, detection_probability=0.9), seed=11)
    second = _run(scene, sensor=_sensor(bearing_sigma_deg=0.5, detection_probability=0.9), seed=11)

    assert first == second
    assert first.measurements.trial_id == first.truth.trial_id == "a15"
    assert len(first.truth.objects) == 2
    assert first.diagnostics.in_range_physical_count == 2
    assert first.diagnostics.detected_physical_group_count <= 2
    assert {field.name for field in fields(first.measurements)} == {
        "measurements",
        "trial_id",
        "covariance_deg2",
        "calibrated_common_bias_mean_deg",
        "calibrated_common_bias_sigma_deg",
        "schema_version",
    }
    assert all(not hasattr(item, "object_id") for item in first.measurements.measurements)
    assert all(not hasattr(item, "range_m") for item in first.measurements.measurements)
    assert all(not hasattr(item, "observer_east_m") for item in first.measurements.measurements)


def test_type_labels_on_off_are_paired_without_changing_bearings_or_detections():
    scene = PhysicalSceneV2(_catalog(_record("one", 10.0, 1_000.0)))
    sensor = _sensor(
        bearing_sigma_deg=0.5,
        detection_probability=1.0,
        type_error_probability=0.5,
        expected_clutter_reports=1.0,
        outlier_probability=0.05,
    )
    labeled = _run(scene, sensor=sensor, seed=20261025)
    unlabeled = _run(
        scene,
        sensor=SensorConfigurationV2(
            **{
                field.name: (
                    False if field.name == "report_type_labels" else getattr(sensor, field.name)
                )
                for field in fields(sensor)
            }
        ),
        seed=20261025,
    )

    assert len(labeled.measurements.measurements) == len(unlabeled.measurements.measurements)
    assert [item.bearing_deg for item in labeled.measurements.measurements] == [
        item.bearing_deg for item in unlabeled.measurements.measurements
    ]
    assert [item.sigma_deg for item in labeled.measurements.measurements] == [
        item.sigma_deg for item in unlabeled.measurements.measurements
    ]
    assert labeled.measurements.measurements[0].object_type is not None
    assert unlabeled.measurements.measurements[0].object_type is None


def test_heading_bias_changes_global_bearing_without_heading_rotation_or_other_noise():
    baseline = _run(seed=101)
    biased = _run(mismatch=SimulatorMismatchConfig(heading_bias_deg=7.0), seed=101)

    assert biased.measurements.measurements[0].bearing_deg == pytest.approx(
        (baseline.measurements.measurements[0].bearing_deg + 7.0) % 360.0
    )
    assert biased.truth.observer_heading_deg == 123.0


def test_catalog_displacement_moves_physical_truth_relative_to_search_catalog():
    scene = PhysicalSceneV2(_catalog(_record("one", 0.0, 1_000.0)))
    base = _run(scene)
    displaced = _run(
        scene,
        mismatch=SimulatorMismatchConfig(catalog_displacement_east_m=100.0),
    )

    assert base.measurements.measurements[0].bearing_deg == pytest.approx(0.0, abs=0.01)
    assert displaced.measurements.measurements[0].bearing_deg != pytest.approx(0.0, abs=0.01)
    assert scene.catalog.records[0].longitude_deg == pytest.approx(
        _record("one", 0.0, 1_000.0).longitude_deg
    )
    base_object, moved_object = base.truth.objects[0], displaced.truth.objects[0]
    assert moved_object.east_m - base_object.east_m == pytest.approx(100.0, abs=0.1)


def test_alternate_observation_map_changes_los_without_mutating_search_map():
    scene = PhysicalSceneV2(_catalog(_record("behind-land", 0.0, 1_500.0)))
    east, north = _TO_XY.transform(*ORIGIN)
    observation_map = _map(land=box(east - 100, north + 900, east + 100, north + 1_100))
    search_map = _map()
    run = _run(
        scene,
        vector_map=search_map,
        mismatch=SimulatorMismatchConfig(observation_map=observation_map),
    )

    assert run.measurements.measurements == ()
    assert run.diagnostics.in_range_physical_count == 1
    assert run.diagnostics.line_of_sight_visible_physical_count == 0
    assert search_map.land_geometry.is_empty


def test_range_is_inclusive_and_angular_extent_is_not_sigma():
    metadata = (("angular_half_width_deg", 0.4), ("position_uncertainty_m", 20.0))
    scene = PhysicalSceneV2(_catalog(_record("edge", 30.0, 1_000.0, metadata=metadata)))
    run = _run(scene, sensor=_sensor(visibility_radius_m=1_000.0, bearing_sigma_deg=0.5))

    measurement = run.measurements.measurements[0]
    assert run.diagnostics.in_range_physical_count == 1
    assert measurement.sigma_deg == 0.5
    assert measurement.angular_half_width_deg == 0.4


def test_merge_keeps_nearest_catalog_representative_and_truth_membership():
    scene = PhysicalSceneV2(
        _catalog(
            _record("far", 0.05, 1_200.0),
            _record("near", 0.0, 700.0),
        )
    )
    run = _run(scene, sensor=_sensor(angular_resolution_deg=0.1))

    assert len(run.measurements.measurements) == 1
    assert run.diagnostics.merged_physical_group_count == 1
    assert run.diagnostics.detected_physical_group_count == 1
    group = run.diagnostics.groups[0]
    assert set(group.member_object_ids) == {"far", "near"}
    assert group.representative_catalog_key == "near"
    assert group.observation_index == 0
    assert {item.observation_index for item in run.truth.objects} == {0}


def test_detection_drop_occurs_after_merge_and_retains_truth_group_diagnostic():
    scene = PhysicalSceneV2(
        _catalog(
            _record("a", 0.02, 700.0),
            _record("b", 0.04, 800.0),
        )
    )
    run = _run(scene, sensor=_sensor(angular_resolution_deg=0.1, detection_probability=0.0))

    assert run.measurements.measurements == ()
    assert run.diagnostics.merged_physical_group_count == 1
    assert run.diagnostics.detected_physical_group_count == 0
    assert run.diagnostics.groups[0].catalog_member_keys == ("a", "b")
    assert run.diagnostics.groups[0].observation_index is None
    assert all(item.observation_index is None for item in run.truth.objects)


def test_extra_reports_are_vessels_and_respect_disabled_labels():
    sensor = _sensor(detection_probability=0.0, expected_clutter_reports=10.0)
    run = _run(sensor=sensor, seed=11)
    assert run.diagnostics.clutter_report_count > 0
    assert all(item.object_type == "vessel" for item in run.measurements.measurements)

    unlabeled = _run(
        sensor=_sensor(
            detection_probability=0.0,
            expected_clutter_reports=10.0,
            report_type_labels=False,
        ),
        seed=11,
    )
    assert len(unlabeled.measurements.measurements) == len(run.measurements.measurements)
    assert all(item.object_type is None for item in unlabeled.measurements.measurements)
