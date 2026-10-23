"""Standalone search methods honor shared map constraints."""

from __future__ import annotations

import numpy as np
import pytest

from bearing_localization.search_cli import run_search_files
from maritime_localization_shared.contracts import (
    AISRecord,
    AISSnapshot,
    MapInput,
    MeasurementSet,
    Observation,
)
from maritime_localization_shared.serialization import write_contract


@pytest.mark.parametrize("algorithm", ["randomized", "hierarchical"])
def test_standalone_search_accepts_map_for_non_grid_methods(tmp_path, algorithm: str) -> None:
    true_xy = np.asarray([100.0, -100.0])
    landmark_xy = np.asarray(
        [[-2_000.0, 500.0], [1_100.0, 1_800.0], [2_400.0, -1_300.0]],
        dtype=float,
    )
    bearings = np.degrees(
        np.arctan2(landmark_xy[:, 0] - true_xy[0], landmark_xy[:, 1] - true_xy[1])
    )
    measurements = MeasurementSet(
        observations=tuple(Observation(float(bearing), 0.1, "vessel") for bearing in bearings),
        trial_id="map-search",
    )
    ais = AISSnapshot(
        records=tuple(
            AISRecord(f"vessel-{index}", float(x), float(y), "vessel")
            for index, (x, y) in enumerate(landmark_xy)
        ),
        timestamp_utc="2026-10-23T12:00:00Z",
        crs="local-EN",
        snapshot_id="map-search",
    )
    water = MapInput(
        crs="local-EN",
        west_m=-5_000.0,
        south_m=-5_000.0,
        cell_size_m=500.0,
        water_mask=tuple(tuple(True for _ in range(20)) for _ in range(20)),
    )
    measurements_path = tmp_path / "measurements.json"
    ais_path = tmp_path / "ais.json"
    map_path = tmp_path / "map.json"
    write_contract(measurements_path, measurements)
    write_contract(ais_path, ais)
    write_contract(map_path, water)

    estimate = run_search_files(
        measurements_path,
        ais_path,
        (-500.0, 500.0, -500.0, 500.0),
        algorithm=algorithm,
        map_path=map_path,
        grid_spacing_m=500.0,
        iterations=500,
        seed=4,
    )

    assert np.hypot(estimate.east_m - true_xy[0], estimate.north_m - true_xy[1]) < 50.0
