"""Accuracy checks for geographic coordinates and local projected frames."""

from __future__ import annotations

from math import hypot

import pytest
from pyproj import Geod

from maritime.geometry.coordinates import (
    LocalFrame,
    geodesic_bearing_distance,
)

_GEOD = Geod(ellps="WGS84")


@pytest.mark.parametrize("bearing_deg", [0.0, 90.0, 180.0, 270.0, 359.0])
def test_geodesic_bearing_distance_uses_clockwise_true_north(bearing_deg: float) -> None:
    origin = (-75.0, 0.0)
    target_lon, target_lat, _ = _GEOD.fwd(*origin, bearing_deg, 12_345.0)
    result = geodesic_bearing_distance(origin, (target_lon, target_lat))
    assert result.true_bearing_deg == pytest.approx(bearing_deg, abs=1e-9)
    assert result.distance_m == pytest.approx(12_345.0, abs=1e-6)
    assert result.grid_bearing_deg == result.true_bearing_deg
    assert result.meridian_convergence_deg == 0.0
    assert result.bearing_error_deg == 0.0


def test_geodesic_bearing_wraps_across_antimeridian() -> None:
    result = geodesic_bearing_distance((179.9, 10.0), (-179.9, 10.0))
    assert result.true_bearing_deg == pytest.approx(89.982635, abs=0.002)
    assert 21_000.0 < result.distance_m < 22_000.0


@pytest.mark.parametrize(
    "frame",
    [LocalFrame((-76.3, 37.0)), LocalFrame((-76.3, 37.0), "EPSG:32618")],
)
def test_local_frame_round_trip(frame: LocalFrame) -> None:
    geographic = (-76.275, 37.018)
    east_m, north_m = frame.to_local(geographic)
    recovered = frame.to_geographic(east_m, north_m)
    assert recovered == pytest.approx(geographic, abs=1e-10)
    assert frame.to_local(frame.origin_lonlat) == pytest.approx((0.0, 0.0), abs=1e-8)


def test_neighboring_utm_tiles_round_trip_independently() -> None:
    west_tile = LocalFrame((-72.02, 38.0), "EPSG:32618")
    east_tile = LocalFrame((-71.98, 38.0), "EPSG:32619")
    locations = [(-72.01, 38.01), (-72.0, 37.99), (-71.99, 38.02)]
    for tile in (west_tile, east_tile):
        for location in locations:
            local = tile.to_local(location)
            assert tile.to_geographic(*local) == pytest.approx(location, abs=1e-9)


def test_local_frame_reports_grid_north_separately_from_true_north() -> None:
    frame = LocalFrame((-76.3, 37.0), "EPSG:32618")
    target = frame.to_geographic(5_000.0, 5_000.0)
    result = frame.bearing_distance(target)
    reference = geodesic_bearing_distance(frame.origin_lonlat, target)
    assert result.true_bearing_deg == pytest.approx(reference.true_bearing_deg, abs=1e-12)
    assert result.distance_m == pytest.approx(reference.distance_m, abs=1e-9)
    assert result.grid_bearing_deg == pytest.approx(45.0, abs=1e-9)
    assert result.meridian_convergence_deg == pytest.approx(
        result.true_bearing_deg - result.grid_bearing_deg, abs=1e-12
    )
    assert result.bearing_error_deg == pytest.approx(abs(result.meridian_convergence_deg))
    assert result.bearing_error_deg > 0.01


@pytest.mark.parametrize(
    ("distance_m", "max_planar_error_m"),
    [(1_000.0, 0.5), (5_000.0, 2.0), (10_000.0, 4.0)],
)
def test_utm_planar_distance_approximation_error_over_local_domain(
    distance_m: float, max_planar_error_m: float
) -> None:
    origin = (-76.3, 37.0)
    frame = LocalFrame(origin, "EPSG:32618")
    errors: list[float] = []
    for azimuth_deg in range(0, 360, 45):
        target_lon, target_lat, _ = _GEOD.fwd(*origin, azimuth_deg, distance_m)
        east_m, north_m = frame.to_local((target_lon, target_lat))
        errors.append(abs(hypot(east_m, north_m) - distance_m))
    assert max(errors) < max_planar_error_m
