from __future__ import annotations

import numpy as np
import pytest

from maritime.schemas import (
    AISRecord,
    AISSnapshot,
    MeasurementSet,
    Observation,
)
from maritime.search import PairwiseSearchConfig, run_pairwise_search
from maritime.search.pairwise import pairwise_hypothesis_search


def _fixture(
    observer: tuple[float, float] = (120.0, -80.0),
) -> tuple[MeasurementSet, AISSnapshot]:
    targets = (
        ("ship-a", "ship", (-2_000.0, 500.0)),
        ("buoy-a", "buoy", (1_100.0, 1_800.0)),
        ("marker-a", "channel_marker", (2_400.0, -1_300.0)),
        ("ship-b", "ship", (-1_700.0, -2_200.0)),
    )
    observations = tuple(
        Observation(
            bearing_deg=float(np.degrees(np.arctan2(xy[0] - observer[0], xy[1] - observer[1]))),
            bearing_sigma_deg=0.1,
            object_type=kind,
        )
        for _, kind, xy in targets[:3]
    )
    ais = AISSnapshot(
        records=tuple(AISRecord(record_id, *xy, kind) for record_id, kind, xy in targets),
        timestamp_utc="2026-10-24T12:00:00Z",
        crs="local-EN",
        snapshot_id="pairwise-test",
    )
    return MeasurementSet(observations), ais


def test_pairwise_search_recovers_exact_position_and_matches() -> None:
    measurements, ais = _fixture()
    estimate = run_pairwise_search(
        measurements,
        ais,
        (-500.0, 500.0, -500.0, 500.0),
        PairwiseSearchConfig(max_hypotheses=1_000),
    )

    assert estimate.algorithm == "pairwise_hypothesis"
    assert np.hypot(estimate.east_m - 120.0, estimate.north_m + 80.0) < 1e-5
    assert {ais_id for _, ais_id in estimate.matched_pairs} == {
        "ship-a",
        "buoy-a",
        "marker-a",
    }


def test_pairwise_search_prunes_large_catalog_before_building_target_pairs() -> None:
    measurements, ais = _fixture()
    distractors = tuple(
        AISRecord(f"far-{index}", 50_000.0 + index * 10.0, 60_000.0, "ship")
        for index in range(6_000)
    )
    large_catalog = AISSnapshot(
        records=ais.records + distractors,
        timestamp_utc=ais.timestamp_utc,
        crs=ais.crs,
        snapshot_id="large-pairwise-catalog",
    )

    estimate = run_pairwise_search(
        measurements,
        large_catalog,
        (-500.0, 500.0, -500.0, 500.0),
        PairwiseSearchConfig(visibility_radius_m=10_000.0, max_hypotheses=1_000),
    )

    assert np.hypot(estimate.east_m - 120.0, estimate.north_m + 80.0) < 1e-5


def test_pairwise_search_respects_types_or_can_disable_them() -> None:
    measurements, ais = _fixture()
    wrong_types = AISSnapshot(
        records=tuple(
            AISRecord(record.record_id, record.east_m, record.north_m, "other")
            for record in ais.records
        ),
        timestamp_utc=ais.timestamp_utc,
        crs=ais.crs,
        snapshot_id="all-other-types",
    )

    with pytest.raises(ValueError, match="No valid pairwise intersections"):
        run_pairwise_search(
            measurements,
            wrong_types,
            (-500.0, 500.0, -500.0, 500.0),
            PairwiseSearchConfig(use_types=True),
        )

    estimate = run_pairwise_search(
        measurements,
        wrong_types,
        (-500.0, 500.0, -500.0, 500.0),
        PairwiseSearchConfig(use_types=False),
    )
    assert np.hypot(estimate.east_m - 120.0, estimate.north_m + 80.0) < 1e-5


def test_pairwise_search_applies_water_and_line_of_sight_filters() -> None:
    measurements, ais = _fixture()
    rays: list[tuple[tuple[float, float], tuple[float, float]]] = []

    class Visibility:
        def is_water(self, east_m: float, north_m: float) -> bool:
            return east_m > 0.0

        def has_line_of_sight(
            self,
            observer_east_m: float,
            observer_north_m: float,
            target_east_m: float,
            target_north_m: float,
        ) -> bool:
            rays.append(((observer_east_m, observer_north_m), (target_east_m, target_north_m)))
            return target_east_m >= 0.0

    estimate = run_pairwise_search(
        measurements,
        ais,
        (-500.0, 500.0, -500.0, 500.0),
        PairwiseSearchConfig(max_hypotheses=1_000),
        visibility=Visibility(),
    )
    assert estimate.east_m > 0.0
    assert rays
    assert "ship-a" not in {record_id for _, record_id in estimate.matched_pairs}


def test_pairwise_search_rejects_degenerate_bearing_geometry() -> None:
    points = np.asarray([[0.0, 100.0], [100.0, 200.0]])
    with pytest.raises(ValueError, match="No valid pairwise intersections"):
        pairwise_hypothesis_search(
            [("ship", 0.0), ("ship", 0.0)],
            points,
            ["ship", "ship"],
            (-10.0, 10.0, -10.0, 10.0),
            PairwiseSearchConfig(min_crossing_angle_deg=1.0),
        )


def test_pairwise_search_requires_two_observations() -> None:
    with pytest.raises(ValueError, match="at least two observations"):
        pairwise_hypothesis_search(
            [("ship", 15.0)],
            np.asarray([[0.0, 100.0]]),
            ["ship"],
            (-10.0, 10.0, -10.0, 10.0),
        )
