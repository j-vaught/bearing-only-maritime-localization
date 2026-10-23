"""Tests for the projected-coordinate spatial index."""

from typing import Any

import pytest

from bearing_localization.geospatial.spatial_index import SpatialIndex2D


def test_query_returns_only_points_inside_radius_in_input_order() -> None:
    index = SpatialIndex2D(
        [(0.0, 0.0), (3.0, 4.0), (4.0, 4.0), (-3.0, -4.0)],
        cell_size_m=2.0,
    )

    assert index.query_radius(0.0, 0.0, 5.0) == (0, 1, 3)


def test_radius_boundary_is_inclusive_and_zero_radius_matches_exact_points() -> None:
    index = SpatialIndex2D([(10.0, -2.0), (10.0, -2.0), (10.0, -1.99)])

    assert index.query_radius(0.0, 0.0, 0.0) == ()
    assert index.query_radius(10.0, -2.0, 0.0) == (0, 1)
    assert index.query_radius(10.0, -2.0, 1.0) == (0, 1, 2)


def test_queries_work_across_negative_and_cell_boundary_coordinates() -> None:
    index = SpatialIndex2D([(-10.0, -10.0), (-9.0, -10.0), (0.0, 0.0)], cell_size_m=10.0)

    assert index.query_radius(-10.0, -10.0, 1.0) == (0, 1)


def test_empty_index_returns_no_matches() -> None:
    assert SpatialIndex2D([], cell_size_m=5.0).query_radius(1.0, 2.0, 100.0) == ()


@pytest.mark.parametrize("cell_size", [0.0, -1.0, float("inf"), float("nan")])
def test_cell_size_must_be_finite_and_positive(cell_size: float) -> None:
    with pytest.raises(ValueError, match="cell_size_m"):
        SpatialIndex2D([], cell_size_m=cell_size)


@pytest.mark.parametrize("radius", [-1.0, float("inf"), float("nan")])
def test_radius_must_be_finite_and_nonnegative(radius: float) -> None:
    index = SpatialIndex2D([(0.0, 0.0)])

    with pytest.raises(ValueError, match="radius_m"):
        index.query_radius(0.0, 0.0, radius)


@pytest.mark.parametrize("point", [[1.0], [1.0, 2.0, 3.0], [float("nan"), 2.0]])
def test_points_must_have_two_finite_coordinates(point: Any) -> None:
    with pytest.raises(ValueError):
        SpatialIndex2D([point])


@pytest.mark.parametrize("east,north", [(float("inf"), 0.0), (0.0, float("nan"))])
def test_query_coordinates_must_be_finite(east: float, north: float) -> None:
    index = SpatialIndex2D([(0.0, 0.0)])

    with pytest.raises(ValueError, match="Query coordinates"):
        index.query_radius(east, north, 1.0)
