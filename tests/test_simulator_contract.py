"""Integration checks for reproducible simulator outputs and truth isolation."""

from __future__ import annotations

import json
from dataclasses import fields

import numpy as np
from bearing_localization.simulator.runner import run_simulator

from bearing_localization.scene import (
    Scene,
    SceneConfig,
    ShipPose,
    generate_scene,
)
from maritime_localization_shared.contracts import EvaluationTruth, MeasurementSet
from maritime_localization_shared.serialization import read_contract


def _scene_and_observer() -> tuple[Scene, ShipPose]:
    """Build one stable scene with visible AIS and non-AIS objects."""
    scene = generate_scene(
        SceneConfig(
            width_m=2_000.0,
            height_m=2_000.0,
            object_count=32,
            ais_fraction=0.5,
            visibility_radius_m=1_500.0,
            bearing_noise_sigma_deg=0.25,
            report_type_labels=True,
        ),
        rng=np.random.default_rng(41),
    )
    return scene, ShipPose(east_m=0.0, north_m=0.0, heading_deg=27.0)


def test_same_seed_reproduces_measurements_and_truth() -> None:
    scene, observer = _scene_and_observer()

    first = run_simulator(scene, observer, seed=2026, trial_id="repeatable")
    second = run_simulator(scene, observer, seed=2026, trial_id="repeatable")

    assert first.measurements == second.measurements
    assert first.truth == second.truth
    assert first.measurements.trial_id == first.truth.trial_id == "repeatable"


def test_measurement_does_not_expose_pose_or_object_identity() -> None:
    scene, observer = _scene_and_observer()

    run = run_simulator(scene, observer, seed=17, trial_id="boundary")

    assert isinstance(run.measurements, MeasurementSet)
    assert isinstance(run.truth, EvaluationTruth)
    assert {field.name for field in fields(run.measurements)} == {
        "observations",
        "trial_id",
        "timestamp_utc",
        "schema_version",
    }
    assert {field.name for field in fields(run.measurements.observations[0])} == {
        "bearing_deg",
        "bearing_sigma_deg",
        "object_type",
        "angular_half_width_deg",
    }
    assert not hasattr(run.measurements, "observer_east_m")
    assert not hasattr(run.measurements, "observer_north_m")
    assert not hasattr(run.measurements, "objects")
    assert all(not hasattr(item, "object_id") for item in run.measurements.observations)
    assert all(not hasattr(item, "range_m") for item in run.measurements.observations)

    assert run.truth.observer_east_m == observer.east_m
    assert run.truth.observer_north_m == observer.north_m
    assert run.truth.observer_heading_deg == observer.heading_deg
    assert all(item.object_id for item in run.truth.objects)


def test_seed_changes_noisy_measurements_without_changing_truth() -> None:
    scene, observer = _scene_and_observer()

    first = run_simulator(scene, observer, seed=100)
    second = run_simulator(scene, observer, seed=101)

    assert first.truth == second.truth
    assert first.measurements.observations != second.measurements.observations
    assert len(first.measurements.observations) == len(second.measurements.observations)


def test_measurements_and_truth_are_written_as_separate_contract_files(tmp_path) -> None:
    scene, observer = _scene_and_observer()
    measurements_path = tmp_path / "measurements.json"
    truth_path = tmp_path / "truth.json"

    run = run_simulator(
        scene,
        observer,
        seed=8,
        trial_id="separate-files",
        measurements_path=measurements_path,
        truth_path=truth_path,
    )

    assert measurements_path.exists()
    assert truth_path.exists()
    assert measurements_path != truth_path
    measurement_document = json.loads(measurements_path.read_text(encoding="utf-8"))
    truth_document = json.loads(truth_path.read_text(encoding="utf-8"))
    assert measurement_document["contract_type"] == "MeasurementSet"
    assert truth_document["contract_type"] == "EvaluationTruth"
    assert "observer_east_m" not in measurement_document
    assert "objects" not in measurement_document
    assert "observations" not in truth_document
    assert read_contract(measurements_path, MeasurementSet) == run.measurements
    assert read_contract(truth_path, EvaluationTruth) == run.truth

    measurement_ids = {item.get("object_id") for item in measurement_document["observations"]}
    assert measurement_ids == {None}
