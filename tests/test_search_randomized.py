"""Contract and behavior tests for the randomized search adapter."""

from __future__ import annotations

import numpy as np
import pytest

from maritime.schemas import (
    AISRecord,
    AISSnapshot,
    MeasurementSet,
    Observation,
)
from maritime.search.randomized import RandomizedSearchConfig, randomized_search


def _fixture() -> tuple[MeasurementSet, AISSnapshot]:
    boat = np.array([120.0, -80.0])
    points = [
        ("ship", np.array([-2_000.0, 500.0])),
        ("buoy", np.array([1_100.0, 1_800.0])),
        ("channel_marker", np.array([2_400.0, -1_300.0])),
        ("ship", np.array([-1_700.0, -2_200.0])),
    ]
    observations = tuple(
        Observation(
            bearing_deg=float(np.degrees(np.arctan2(point[0] - boat[0], point[1] - boat[1]))),
            bearing_sigma_deg=0.2,
            object_type=kind,
        )
        for kind, point in points[:3]
    )
    records = tuple(
        AISRecord(
            record_id=f"ais-{index}",
            east_m=float(point[0]),
            north_m=float(point[1]),
            ais_class=kind,
        )
        for index, (kind, point) in enumerate(points)
    )
    return (
        MeasurementSet(observations=observations, trial_id="trial-1"),
        AISSnapshot(
            records=records,
            timestamp_utc="2026-10-23T12:00:00Z",
            crs="local-EN",
            snapshot_id="snapshot-1",
        ),
    )


def test_randomized_adapter_recovers_position_and_maps_matches_to_ais_ids() -> None:
    measurements, ais = _fixture()
    estimate = randomized_search(
        measurements,
        ais,
        RandomizedSearchConfig(bounds_xy=(-500.0, 500.0, -500.0, 500.0), iterations=500, seed=9),
    )

    assert estimate.algorithm == "randomized_hypothesis"
    assert np.hypot(estimate.east_m - 120.0, estimate.north_m + 80.0) < 10.0
    assert {ais_id for _, ais_id in estimate.matched_pairs} == {"ais-0", "ais-1", "ais-2"}
    assert estimate.elapsed_s >= 0.0


def test_randomized_adapter_is_seed_reproducible() -> None:
    measurements, ais = _fixture()
    config = RandomizedSearchConfig(
        bounds_xy=(-500.0, 500.0, -500.0, 500.0), iterations=100, seed=14
    )

    first = randomized_search(measurements, ais, config)
    second = randomized_search(measurements, ais, config)

    assert (first.east_m, first.north_m, first.score, first.matched_pairs) == (
        second.east_m,
        second.north_m,
        second.score,
        second.matched_pairs,
    )


def test_randomized_adapter_rejects_land_hypotheses_and_checks_sight_lines() -> None:
    measurements, ais = _fixture()

    class Map:
        checked_rays = 0

        def is_water(self, east_m: float, north_m: float) -> bool:
            _ = north_m
            return east_m >= 0.0

        def has_line_of_sight(
            self,
            observer_east_m: float,
            observer_north_m: float,
            target_east_m: float,
            target_north_m: float,
        ) -> bool:
            _ = observer_east_m, observer_north_m, target_east_m, target_north_m
            self.checked_rays += 1
            return True

    map_queries = Map()
    estimate = randomized_search(
        measurements,
        ais,
        RandomizedSearchConfig(
            bounds_xy=(-500.0, 500.0, -500.0, 500.0),
            iterations=500,
            seed=9,
            visibility=map_queries,
        ),
    )

    assert estimate.east_m >= 0.0
    assert map_queries.checked_rays > 0


@pytest.mark.parametrize("field", ["measurements", "ais", "config"])
def test_randomized_adapter_rejects_wrong_contract_types(field: str) -> None:
    measurements, ais = _fixture()
    config = RandomizedSearchConfig(bounds_xy=(-500.0, 500.0, -500.0, 500.0))
    arguments: dict[str, object] = {
        "measurements": measurements,
        "ais": ais,
        "config": config,
    }
    arguments[field] = object()

    with pytest.raises(TypeError):
        randomized_search(**arguments)  # type: ignore[arg-type]
