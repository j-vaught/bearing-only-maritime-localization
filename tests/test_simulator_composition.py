"""End-to-end composition checks for the modular simulator components."""

from __future__ import annotations

import numpy as np

from maritime.schemas import MapInput
from maritime.simulator import (
    SceneBuilderConfig,
    build_scene,
    run_simulator,
    sample_observers,
    to_ship_pose,
    to_simulator_scene,
)


def test_builder_observer_adapter_and_runner_compose_with_truth_isolation() -> None:
    built = build_scene(
        SceneBuilderConfig(
            east_bounds_m=(-500.0, 500.0),
            north_bounds_m=(-500.0, 500.0),
            ais_count=8,
            non_ais_count=5,
        ),
        seed=31,
    )
    water = MapInput(
        crs="local-EN",
        west_m=-1_000.0,
        south_m=-1_000.0,
        cell_size_m=2_000.0,
        water_mask=((True,),),
    )
    observer_position = sample_observers(water, 1, np.random.default_rng(9))[0]
    observer = to_ship_pose(observer_position, heading_deg=42.0)
    runner_scene = to_simulator_scene(
        built,
        visibility_radius_m=2_000.0,
        bearing_noise_sigma_deg=0.2,
    )

    run = run_simulator(
        runner_scene,
        observer,
        seed=77,
        trial_id="modular-composition",
    )

    assert run.measurements.trial_id == run.truth.trial_id == "modular-composition"
    assert len(run.ais_snapshot.records) == len(built.ais_snapshot.records)
    assert len(run.truth.objects) == len(built.truth_objects)
    assert (observer.east_m, observer.north_m) == (0.0, 0.0)
    assert all(-180.0 <= item.bearing_deg < 180.0 for item in run.measurements.observations)
    assert all(not hasattr(item, "object_id") for item in run.measurements.observations)
    assert all(
        item.observation_index is None
        or item.observation_index < len(run.measurements.observations)
        for item in run.truth.objects
    )
