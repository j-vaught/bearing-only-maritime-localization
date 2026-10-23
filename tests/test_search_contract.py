"""Search-level correctness checks at the estimator's public contract boundary."""

from __future__ import annotations

from math import atan2, degrees

import numpy as np
import pytest

from bearing_localization.search.association import associate_observations_to_ais
from bearing_localization.search.grid import GridSearchConfig, run_grid_search
from maritime_localization_shared.contracts import (
    AISRecord,
    AISSnapshot,
    MeasurementSet,
    Observation,
)


def _bearing(observer_xy: tuple[float, float], target_xy: tuple[float, float]) -> float:
    """Return a global bearing in degrees clockwise from north."""
    east_delta = target_xy[0] - observer_xy[0]
    north_delta = target_xy[1] - observer_xy[1]
    return degrees(atan2(east_delta, north_delta))


def _snapshot(records: tuple[AISRecord, ...]) -> AISSnapshot:
    return AISSnapshot(
        records=records,
        timestamp_utc="2026-10-23T12:00:00Z",
        crs="local-test",
        snapshot_id="search-contract-test",
    )


def test_grid_search_recovers_known_position_from_global_bearings() -> None:
    truth = (200.0, -100.0)
    targets = (
        ("vessel-a", "ship", (-1_800.0, 2_200.0)),
        ("vessel-b", "ship", (2_500.0, 1_400.0)),
        ("aid-c", "buoy", (1_700.0, -2_300.0)),
    )
    ais_records = tuple(
        AISRecord(record_id, *position, ais_class) for record_id, ais_class, position in targets
    )
    ais = _snapshot(ais_records)
    measurements = MeasurementSet(
        observations=tuple(
            Observation(_bearing(truth, position), 0.05, ais_class)
            for _, ais_class, position in targets
        )
    )

    estimate = run_grid_search(
        measurements,
        ais,
        (-500.0, 500.0, -500.0, 500.0),
        GridSearchConfig(grid_spacing_m=100.0, refine=False),
    )

    assert np.hypot(estimate.east_m - truth[0], estimate.north_m - truth[1]) <= 1e-6
    assert set(ais_id for _, ais_id in estimate.matched_pairs) == {
        "vessel-a",
        "vessel-b",
        "aid-c",
    }


def test_single_bearing_is_ambiguous_along_the_same_ray() -> None:
    target = AISRecord("vessel-a", 0.0, 5_000.0, "ship")
    observation = Observation(0.0, 0.1, "ship")

    near = associate_observations_to_ais((0.0, 0.0), (observation,), (target,))
    far = associate_observations_to_ais((0.0, -2_000.0), (observation,), (target,))

    assert near.cost == far.cost == 0.0
    assert near.matches == far.matches == ((0, 0),)
    assert np.hypot(0.0 - 0.0, -2_000.0 - 0.0) == 2_000.0


def test_insufficient_observations_do_not_create_ais_matches() -> None:
    ais = _snapshot((AISRecord("vessel-a", 0.0, 1_000.0, "ship"),))
    measurements = MeasurementSet(observations=())

    with pytest.raises(ValueError, match="at least one observation"):
        run_grid_search(
            measurements,
            ais,
            (-100.0, 100.0, -100.0, 100.0),
            GridSearchConfig(grid_spacing_m=100.0, refine=False),
        )


def test_type_labels_select_matching_class_and_blind_mode_ignores_them() -> None:
    # Both AIS objects produce the same exact bearing. The sensor label is the
    # only information that identifies which class generated the report.
    ais = (
        AISRecord("ship-id", 0.0, 1_000.0, "ship"),
        AISRecord("buoy-id", 0.0, 2_000.0, "buoy"),
    )
    observation = Observation(0.0, 0.1, "buoy")

    labeled = associate_observations_to_ais((0.0, 0.0), (observation,), ais, use_types=True)
    blind = associate_observations_to_ais((0.0, 0.0), (observation,), ais, use_types=False)

    assert labeled.matches == ((0, 1),)
    assert blind.matches == ((0, 0),)


def test_untracked_detection_remains_unmatched_while_ais_features_localize() -> None:
    truth = (100.0, -200.0)
    targets = (
        ("vessel-a", "ship", (-1_200.0, 1_800.0)),
        ("vessel-b", "ship", (2_100.0, 1_200.0)),
        ("aid-c", "buoy", (1_500.0, -1_700.0)),
    )
    ais_records = tuple(
        AISRecord(record_id, *position, ais_class) for record_id, ais_class, position in targets
    )
    ais = _snapshot(ais_records)
    ais_observations = tuple(
        Observation(_bearing(truth, position), 0.05, ais_class)
        for _, ais_class, position in targets
    )
    non_ais_observation = Observation(17.0, 0.05, "shore_structure")
    measurements = MeasurementSet(observations=ais_observations + (non_ais_observation,))

    estimate = run_grid_search(
        measurements,
        ais,
        (-500.0, 500.0, -500.0, 500.0),
        GridSearchConfig(grid_spacing_m=100.0, refine=False),
    )

    assert np.hypot(estimate.east_m - truth[0], estimate.north_m - truth[1]) <= 1e-6
    assert set(ais_id for _, ais_id in estimate.matched_pairs) == {
        "vessel-a",
        "vessel-b",
        "aid-c",
    }
    assert all(
        observation_index != len(ais_observations)
        for observation_index, _ in estimate.matched_pairs
    )
