"""Cross-check raster visibility against the vector reference on simple grids."""

from __future__ import annotations

import pytest

from bearing_localization.geospatial import line_of_sight_clear, raster_line_of_sight
from maritime_localization_shared.contracts.map_input import MapInput


@pytest.mark.parametrize(
    ("start", "end", "expected_clear"),
    [
        ((0.25, 1.5), (4.75, 1.5), False),
        ((0.25, 0.5), (4.75, 0.5), True),
        ((0.25, 0.25), (4.75, 0.75), True),
        ((0.25, 2.5), (4.75, 2.5), True),
        ((1.5, 0.25), (1.5, 2.75), True),
    ],
)
def test_raster_and_vector_visibility_agree_for_cell_aligned_land(
    start: tuple[float, float], end: tuple[float, float], expected_clear: bool
) -> None:
    # The central cell is land. Its polygon is the exact unit-square footprint.
    water = tuple(
        tuple(not (row == 1 and col == 2) for col in range(5)) for row in range(3)
    )
    map_input = MapInput(
        crs="local-metre-grid",
        west_m=0.0,
        south_m=0.0,
        cell_size_m=1.0,
        water_mask=water,
    )
    central_land = (((2.0, 1.0), (3.0, 1.0), (3.0, 2.0), (2.0, 2.0)),)

    vector_clear = line_of_sight_clear(start, end, central_land)
    raster_clear = raster_line_of_sight(map_input, start, end)

    assert vector_clear is expected_clear
    assert raster_clear is expected_clear
