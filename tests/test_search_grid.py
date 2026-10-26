from __future__ import annotations

import pytest

from maritime.schemas import (
    AISRecord,
    AISSnapshot,
    MeasurementSet,
    Observation,
)
from maritime.search.grid import GridSearchConfig, run_grid_search


def _ais_snapshot() -> AISSnapshot:
    return AISSnapshot(
        records=(
            AISRecord("vessel-a", 1_000.0, 0.0, "ship"),
            AISRecord("vessel-b", 0.0, 1_000.0, "ship"),
            AISRecord("aid-c", 1_000.0, 1_000.0, "buoy"),
        ),
        timestamp_utc="2023-07-15T12:00:00Z",
        crs="local-test",
        snapshot_id="test-snapshot",
    )


def _measurements() -> MeasurementSet:
    return MeasurementSet(
        observations=(
            Observation(90.0, 0.1, "ship"),
            Observation(0.0, 0.1, "ship"),
            Observation(45.0, 0.1, "buoy"),
        )
    )


class _Visibility:
    def __init__(
        self,
        *,
        blocked_east: float | None = None,
        block_sight: bool = False,
        all_land: bool = False,
    ) -> None:
        self.blocked_east = blocked_east
        self.block_sight = block_sight
        self.all_land = all_land

    def is_water(self, east_m: float, north_m: float) -> bool:
        return not self.all_land and (self.blocked_east is None or east_m != self.blocked_east)

    def has_line_of_sight(
        self,
        observer_east_m: float,
        observer_north_m: float,
        target_east_m: float,
        target_north_m: float,
    ) -> bool:
        return not self.block_sight


def test_grid_adapter_returns_shared_estimate_with_ais_ids() -> None:
    estimate = run_grid_search(
        _measurements(),
        _ais_snapshot(),
        (-100.0, 100.0, -100.0, 100.0),
        GridSearchConfig(grid_spacing_m=100.0, refine=False),
    )

    assert estimate.east_m == 0.0
    assert estimate.north_m == 0.0
    assert estimate.algorithm == "grid"
    assert {ais_id for _, ais_id in estimate.matched_pairs} == {
        "vessel-a",
        "vessel-b",
        "aid-c",
    }
    assert estimate.elapsed_s >= 0.0


def test_visibility_skips_land_and_occluded_ais_objects() -> None:
    ais = _ais_snapshot()
    # The water mask behavior is exercised structurally; only points with
    # east_m != 0 are water in this region, so the optimum at the origin is
    # unavailable and the estimator must select the nearest valid candidate.
    water_only_origin = _Visibility(blocked_east=0.0)
    measurements = _measurements()
    estimate = run_grid_search(
        measurements,
        ais,
        (-100.0, 100.0, -100.0, 100.0),
        GridSearchConfig(grid_spacing_m=100.0, refine=False),
        visibility=water_only_origin,
    )
    assert estimate.east_m != 0.0

    occluded = run_grid_search(
        measurements,
        ais,
        (0.0, 1.0, 0.0, 1.0),
        GridSearchConfig(grid_spacing_m=1.0, refine=False),
        visibility=_Visibility(block_sight=True),
    )
    assert occluded.matched_pairs == ()


def test_visibility_requires_at_least_one_water_candidate() -> None:
    with pytest.raises(ValueError, match="No water grid candidates"):
        run_grid_search(
            _measurements(),
            _ais_snapshot(),
            (0.0, 100.0, 0.0, 100.0),
            GridSearchConfig(grid_spacing_m=100.0, refine=False),
            visibility=_Visibility(all_land=True),
        )


def test_invalid_bounds_are_rejected() -> None:
    with pytest.raises(ValueError, match="Search bounds"):
        run_grid_search(_measurements(), _ais_snapshot(), (1.0, 1.0, 0.0, 2.0))
