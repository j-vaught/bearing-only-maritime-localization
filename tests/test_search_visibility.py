"""Candidate visibility tests for search-side range and land filtering."""

from __future__ import annotations

import numpy as np
import pytest

from maritime.geometry.water_mask import WaterMask
from maritime.search.visibility import filter_visible_candidates


class BoxLineOfSight:
    """Test query that blocks targets east of a vertical shoreline."""

    def __init__(self) -> None:
        self.checked: list[tuple[tuple[float, float], tuple[float, float]]] = []

    def is_clear(
        self, observer_xy_m: tuple[float, float], target_xy_m: tuple[float, float]
    ) -> bool:
        self.checked.append((observer_xy_m, target_xy_m))
        return target_xy_m[0] < 5.0 or target_xy_m[0] >= 10.0


def test_filters_by_inclusive_range_and_preserves_original_indices() -> None:
    candidates = np.array([[3.0, 4.0], [0.0, 5.0], [5.01, 0.0], [3.0, 4.0]])

    visible = filter_visible_candidates((0.0, 0.0), candidates, visibility_radius_m=5.0)

    np.testing.assert_array_equal(visible, [0, 1, 3])


def test_water_query_rejects_observer_on_land_without_calling_los() -> None:
    water = WaterMask(np.array([[False, True]], dtype=np.bool_), 0.0, 0.0, 10.0)
    line_of_sight = BoxLineOfSight()

    visible = filter_visible_candidates(
        (5.0, 5.0),
        [[1.0, 1.0], [20.0, 1.0]],
        water_query=water,
        line_of_sight_query=line_of_sight,
    )

    assert visible.size == 0
    assert line_of_sight.checked == []


def test_applies_los_only_in_range_and_does_not_require_targets_to_be_water() -> None:
    water = WaterMask(np.array([[True, False]], dtype=np.bool_), 0.0, 0.0, 10.0)
    line_of_sight = BoxLineOfSight()
    candidates = [[2.0, 0.0], [5.0, 0.0], [12.0, 0.0], [22.0, 0.0]]

    visible = filter_visible_candidates(
        (1.0, 1.0),
        candidates,
        visibility_radius_m=15.0,
        water_query=water,
        line_of_sight_query=line_of_sight,
    )

    # x=5 is blocked, while x=12 is land according to the water mask but is
    # retained because the LOS query says it is visible from the observer.
    # x=22 is outside range and never passed to the LOS query.
    np.testing.assert_array_equal(visible, [0, 2])
    assert len(line_of_sight.checked) == 3
    assert all(start == (1.0, 1.0) for start, _ in line_of_sight.checked)


def test_missing_map_queries_disable_only_their_own_checks() -> None:
    candidates = [[2.0, 0.0], [12.0, 0.0]]

    visible = filter_visible_candidates((0.0, 0.0), candidates, visibility_radius_m=5.0)

    np.testing.assert_array_equal(visible, [0])


@pytest.mark.parametrize(
    ("observer", "candidates", "radius"),
    [
        ([0.0], [[1.0, 1.0]], 1.0),
        ([0.0, np.nan], [[1.0, 1.0]], 1.0),
        ([0.0, 0.0], [1.0, 1.0], 1.0),
        ([0.0, 0.0], [[1.0, np.inf]], 1.0),
        ([0.0, 0.0], [[1.0, 1.0]], 0.0),
        ([0.0, 0.0], [[1.0, 1.0]], float("nan")),
    ],
)
def test_rejects_invalid_observer_candidates_or_range(
    observer: list[float], candidates: list[list[float]], radius: float
) -> None:
    with pytest.raises(ValueError):
        filter_visible_candidates(observer, candidates, visibility_radius_m=radius)
