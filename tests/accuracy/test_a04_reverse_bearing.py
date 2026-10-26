"""Accuracy and validation checks for reverse true-north geodesics."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from pyproj import Geod

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from maritime_localization_shared.geospatial.reverse_bearing import (  # noqa: E402
    ReverseBearingResult,
    solve_reverse_bearing_observer,
)

_GEOD = Geod(ellps="WGS84")


def test_east_coast_broadcast_solver_matches_independent_inverse_for_bearings_and_ranges() -> None:
    catalog = np.asarray(
        [(-80.2, 25.8), (-77.0, 34.0), (-73.5, 40.7), (-67.0, 44.8)], dtype=np.float64
    )[:, None, None, :]
    bearings = np.asarray([0.0, 45.0, 90.0, 135.0, 180.0, 225.0, 270.0, 315.0])[None, :, None]
    ranges = np.asarray([0.1, 1.0, 100.0, 1_000.0, 10_000.0])[None, None, :]

    result = solve_reverse_bearing_observer(catalog, bearings, ranges)

    assert isinstance(result, ReverseBearingResult)
    assert result.observer_lonlat_deg.shape == (4, 8, 5, 2)
    assert result.angular_residual_deg.shape == (4, 8, 5)
    assert np.all(result.converged[..., 2:])
    assert not np.any(result.degenerate)
    assert np.max(np.abs(result.angular_residual_deg)) < 5e-7
    assert np.array_equal(result.converged, np.abs(result.angular_residual_deg) <= 1e-9)
    inverse_bearing, _, distance = _GEOD.inv(
        result.observer_lonlat_deg[..., 0],
        result.observer_lonlat_deg[..., 1],
        np.broadcast_to(catalog[..., 0], result.angular_residual_deg.shape),
        np.broadcast_to(catalog[..., 1], result.angular_residual_deg.shape),
    )
    expected_bearing = np.broadcast_to(bearings, result.angular_residual_deg.shape)
    wrapped_error = (inverse_bearing - expected_bearing + 180.0) % 360.0 - 180.0
    assert np.max(np.abs(wrapped_error)) < 5e-7
    assert distance == pytest.approx(np.broadcast_to(ranges, distance.shape), abs=1e-5)
    assert not result.observer_lonlat_deg.flags.writeable
    assert not result.angular_residual_deg.flags.writeable
    assert not result.converged.flags.writeable
    assert not result.degenerate.flags.writeable


def test_bearing_wrap_and_antimeridian_polar_edge_cases() -> None:
    catalog = np.asarray([(179.999, 80.0), (-179.99, -89.8), (0.0, 89.9)])
    result = solve_reverse_bearing_observer(
        catalog,
        desired_bearing_deg=np.asarray([-10.0, 710.0, 181.0]),
        range_m=np.asarray([10_000.0, 500.0, 1_000.0]),
    )

    inverse_bearing, _, distance = _GEOD.inv(
        result.observer_lonlat_deg[:, 0],
        result.observer_lonlat_deg[:, 1],
        catalog[:, 0],
        catalog[:, 1],
    )
    expected = np.asarray([350.0, 350.0, 181.0])
    error = (inverse_bearing - expected + 180.0) % 360.0 - 180.0
    assert np.all(result.converged)
    assert np.max(np.abs(error)) <= 1e-9
    assert distance == pytest.approx([10_000.0, 500.0, 1_000.0], abs=1e-5)
    assert np.all(
        (-180.0 <= result.observer_lonlat_deg[:, 0]) & (result.observer_lonlat_deg[:, 0] <= 180.0)
    )
    assert np.all(
        (-90.0 <= result.observer_lonlat_deg[:, 1]) & (result.observer_lonlat_deg[:, 1] <= 90.0)
    )


def test_zero_range_is_explicitly_degenerate_and_keeps_finite_coordinates() -> None:
    catalog = np.asarray([(-75.0, 35.0), (179.99, -89.5)])
    result = solve_reverse_bearing_observer(catalog, [0.0, 270.0], [0.0, 0.0])

    assert np.array_equal(result.observer_lonlat_deg, catalog)
    assert np.array_equal(result.angular_residual_deg, [0.0, 0.0])
    assert np.array_equal(result.degenerate, [True, True])
    assert np.array_equal(result.converged, [False, False])
    assert np.all(np.isfinite(result.observer_lonlat_deg))


def test_nonunique_global_range_failure_is_explicit_and_finite() -> None:
    result = solve_reverse_bearing_observer((179.9, 89.9), 45.0, 20_000_000.0)

    assert not bool(result.converged)
    assert not bool(result.degenerate)
    assert np.all(np.isfinite(result.observer_lonlat_deg))
    assert np.isfinite(result.angular_residual_deg)
    assert abs(float(result.angular_residual_deg)) > 1e-9


def test_iteration_limit_reports_unconverged_residual_instead_of_guessing() -> None:
    result = solve_reverse_bearing_observer(
        (-75.0, 40.0),
        desired_bearing_deg=73.0,
        range_m=10_000.0,
        max_iterations=1,
    )

    assert result.converged.shape == ()
    assert not bool(result.converged)
    assert not bool(result.degenerate)
    assert np.isfinite(result.angular_residual_deg)
    assert abs(float(result.angular_residual_deg)) > 1e-9
    assert np.all(np.isfinite(result.observer_lonlat_deg))


@pytest.mark.parametrize(
    ("catalog", "bearing", "distance", "message"),
    [
        ((181.0, 0.0), 0.0, 1.0, "longitude"),
        ((0.0, 91.0), 0.0, 1.0, "latitude"),
        ((float("nan"), 0.0), 0.0, 1.0, "finite"),
        ((0.0, 0.0), float("inf"), 1.0, "finite"),
        ((0.0, 0.0), 0.0, float("nan"), "finite"),
        ((0.0, 0.0), 0.0, -1.0, "nonnegative"),
        ((0.0,), 0.0, 1.0, "final axis"),
        ((0.0, 0.0), 0.0, True, "finite numeric"),
    ],
)
def test_invalid_coordinates_bearings_and_ranges_are_rejected(
    catalog: Any, bearing: Any, distance: Any, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        solve_reverse_bearing_observer(catalog, bearing, distance)


def test_nonbroadcastable_inputs_and_iteration_options_are_rejected() -> None:
    with pytest.raises(ValueError, match="broadcast-compatible"):
        solve_reverse_bearing_observer([(0.0, 0.0), (1.0, 1.0)], [0.0, 90.0, 180.0], 1_000.0)
    with pytest.raises(ValueError, match="max_iterations"):
        solve_reverse_bearing_observer((0.0, 0.0), 0.0, 1.0, max_iterations=13)
    with pytest.raises(ValueError, match="tolerance_deg"):
        solve_reverse_bearing_observer((0.0, 0.0), 0.0, 1.0, tolerance_deg=0.0)


def test_internal_margin_keeps_strict_tolerance_after_inverse_check() -> None:
    catalog = (-75.3929250000001, 39.18361940000068)
    desired_bearing = 205.725571474809
    range_m = 2_875.0

    strict = solve_reverse_bearing_observer(catalog, desired_bearing, range_m)
    explicit = solve_reverse_bearing_observer(
        catalog,
        desired_bearing,
        range_m,
        tolerance_deg=1e-7,
    )

    assert bool(strict.converged)
    assert abs(float(strict.angular_residual_deg)) <= 1e-9
    assert bool(explicit.converged)
    inverse_bearing, _, distance = _GEOD.inv(
        float(explicit.observer_lonlat_deg[0]),
        float(explicit.observer_lonlat_deg[1]),
        catalog[0],
        catalog[1],
    )
    angular_error = (inverse_bearing - desired_bearing + 180.0) % 360.0 - 180.0
    assert abs(angular_error) <= 1e-7
    assert distance == pytest.approx(range_m, abs=1e-8)


def test_full_catalog_roundoff_failure_is_resolved_with_public_tolerance() -> None:
    # Captured from the coordinate-only replay of the frozen A26 input set.
    catalog = (-80.98065189999943, 31.796930200000133)
    desired_bearing = 215.65789052601954
    range_m = 625.0

    result = solve_reverse_bearing_observer(catalog, desired_bearing, range_m, tolerance_deg=1e-7)

    inverse_bearing, _, distance = _GEOD.inv(
        float(result.observer_lonlat_deg[0]),
        float(result.observer_lonlat_deg[1]),
        catalog[0],
        catalog[1],
    )
    angular_error = (inverse_bearing - desired_bearing + 180.0) % 360.0 - 180.0
    assert bool(result.converged)
    assert abs(float(result.angular_residual_deg)) <= 1e-7
    assert abs(angular_error) <= 1e-7
    assert distance == pytest.approx(range_m, abs=1e-8)
