from __future__ import annotations

import numpy as np
import pytest

from maritime.geometry.raster_los import raster_line_of_sight
from maritime.geometry.water_mask import WaterMask
from maritime.schemas.map_input import MapInput


def make_map(mask: tuple[tuple[bool, ...], ...]) -> MapInput:
    return MapInput(
        crs="local-metre-grid",
        west_m=0.0,
        south_m=0.0,
        cell_size_m=1.0,
        water_mask=mask,
    )


def test_water_only_ray_is_visible() -> None:
    grid = make_map(((True, True, True), (True, True, True), (True, True, True)))

    assert raster_line_of_sight(grid, (0.2, 0.3), (2.8, 2.6))


def test_intermediate_land_blocks_visibility() -> None:
    grid = make_map(((True, True, True), (True, False, True), (True, True, True)))

    assert not raster_line_of_sight(grid, (0.2, 1.5), (2.8, 1.5))


def test_land_in_endpoint_cells_does_not_block_target_visibility() -> None:
    grid = make_map(((False, True, False),))

    assert raster_line_of_sight(grid, (0.5, 0.5), (2.5, 0.5))


def test_corner_crossing_checks_both_side_cells() -> None:
    # The diagonal goes through the shared corner; the southeast side cell is land.
    grid = make_map(((True, False), (True, True)))

    assert not raster_line_of_sight(grid, (0.5, 0.5), (1.5, 1.5))


@pytest.mark.parametrize(
    ("start", "end", "land_cell"),
    [
        ((0.5, 0.2), (0.5, 2.8), (0, 1)),
        ((0.2, 0.5), (2.8, 0.5), (1, 0)),
    ],
)
def test_axis_aligned_rays_detect_intermediate_land(
    start: tuple[float, float], end: tuple[float, float], land_cell: tuple[int, int]
) -> None:
    mask = [[True for _ in range(3)] for _ in range(3)]
    col, row = land_cell
    mask[row][col] = False
    grid = make_map(tuple(tuple(row_cells) for row_cells in mask))

    assert not raster_line_of_sight(grid, start, end)


def test_outside_endpoints_are_not_visible() -> None:
    grid = make_map(((True, True), (True, True)))

    assert not raster_line_of_sight(grid, (-0.01, 0.5), (1.5, 0.5))
    assert not raster_line_of_sight(grid, (0.5, 0.5), (2.0, 0.5))


def test_nodata_blocks_intermediate_cells_and_endpoints() -> None:
    grid = make_map(((True, True, True),))
    nodata = ((False, True, False),)

    assert not raster_line_of_sight(grid, (0.5, 0.5), (2.5, 0.5), nodata_mask=nodata)
    assert not raster_line_of_sight(
        grid, (0.5, 0.5), (2.5, 0.5), nodata_mask=((True, False, False),)
    )


def test_nodata_shape_must_match_water_mask() -> None:
    grid = make_map(((True, True), (True, True)))

    with pytest.raises(ValueError, match="dimensions"):
        raster_line_of_sight(grid, (0.2, 0.2), (1.8, 1.8), nodata_mask=((False,),))


def test_endpoint_on_east_or_north_edge_is_outside() -> None:
    grid = make_map(((True, True), (True, True)))

    assert not raster_line_of_sight(grid, (0.5, 0.5), (2.0, 1.5))
    assert not raster_line_of_sight(grid, (0.5, 0.5), (1.5, 2.0))


def test_water_mask_can_back_raster_ray_traversal() -> None:
    mask = WaterMask(
        water=np.asarray([[True, False, True]], dtype=bool),
        west_m=0.0,
        south_m=0.0,
        cell_size_m=1.0,
    )

    assert not raster_line_of_sight(mask, (0.5, 0.5), (2.5, 0.5))


def test_invalid_water_mask_endpoint_is_not_visible() -> None:
    mask = WaterMask(
        water=np.asarray([[True, True]], dtype=bool),
        west_m=0.0,
        south_m=0.0,
        cell_size_m=1.0,
        valid=np.asarray([[False, True]], dtype=bool),
    )

    assert not raster_line_of_sight(mask, (0.5, 0.5), (1.5, 0.5))
