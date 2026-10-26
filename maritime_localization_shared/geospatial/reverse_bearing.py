"""Solve observer positions from a catalog point, true bearing, and geodesic range."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

import numpy as np
from numpy.typing import ArrayLike, NDArray
from pyproj import Geod
from pyproj.exceptions import GeodError

_GEOD = Geod(ellps="WGS84")


@dataclass(frozen=True, slots=True)
class ReverseBearingResult:
    """Vectorized reverse-ray solutions and explicit convergence status.

    ``observer_lonlat_deg`` has the broadcast input shape followed by a
    longitude/latitude axis. Residuals use the signed convention
    ``inverse_bearing(observer, catalog) - desired_bearing``, wrapped to
    ``[-180, 180)``. Zero-range cases return the catalog position with a zero
    residual placeholder, ``degenerate=True``, and ``converged=False`` because
    a bearing is undefined at zero distance. Every output array is read-only.
    """

    observer_lonlat_deg: NDArray[np.float64]
    angular_residual_deg: NDArray[np.float64]
    converged: NDArray[np.bool_]
    degenerate: NDArray[np.bool_]


def solve_reverse_bearing_observer(
    catalog_lonlat_deg: ArrayLike,
    desired_bearing_deg: ArrayLike,
    range_m: ArrayLike,
    *,
    tolerance_deg: float = 1e-9,
    max_iterations: int = 12,
) -> ReverseBearingResult:
    """Solve the unknown observer point on a WGS84 geodesic.

    The desired bearing is the initial true-north bearing from the unknown
    observer toward the known catalog point. Inputs broadcast over their
    leading dimensions. ``catalog_lonlat_deg`` must end in a pair ordered as
    ``(longitude, latitude)``. Bearings are normalized modulo 360 degrees.

    Each nonzero-range solution starts with ``desired_bearing + 180`` at the
    catalog point. The start azimuth is corrected from the forward back-azimuth
    residual. Iteration stops at half the requested tolerance to leave a
    numerical margin for the independent final ``Geod.inv`` check. ``converged``
    is true only when that check meets the full ``tolerance_deg``. Iteration is
    capped at 12. A false result retains the last finite position and residual
    for explicit caller handling.
    """
    catalog = _as_finite_float_array(catalog_lonlat_deg, "catalog_lonlat_deg")
    if catalog.ndim < 1 or catalog.shape[-1] != 2:
        raise ValueError("catalog_lonlat_deg must have a final axis of length two.")
    if catalog.size == 0:
        raise ValueError("catalog_lonlat_deg must contain at least one position.")
    longitude = catalog[..., 0]
    latitude = catalog[..., 1]
    if np.any((longitude < -180.0) | (longitude > 180.0)):
        raise ValueError("catalog longitude must be within [-180, 180] degrees.")
    if np.any((latitude < -90.0) | (latitude > 90.0)):
        raise ValueError("catalog latitude must be within [-90, 90] degrees.")

    bearing = _as_finite_float_array(desired_bearing_deg, "desired_bearing_deg")
    distance = _as_finite_float_array(range_m, "range_m")
    if np.any(distance < 0.0):
        raise ValueError("range_m must be nonnegative.")
    if not isfinite(tolerance_deg) or tolerance_deg <= 0.0:
        raise ValueError("tolerance_deg must be finite and positive.")
    if type(max_iterations) is not int or not 1 <= max_iterations <= 12:
        raise ValueError("max_iterations must be an integer in [1, 12].")

    try:
        longitude, latitude, bearing, distance = np.broadcast_arrays(
            longitude, latitude, bearing, distance
        )
    except ValueError as exc:
        raise ValueError(
            "catalog positions, desired_bearing_deg, and range_m must be broadcast-compatible."
        ) from exc

    output_shape = longitude.shape
    source_lon = np.asarray(longitude, dtype=np.float64).reshape(-1)
    source_lat = np.asarray(latitude, dtype=np.float64).reshape(-1)
    desired = np.asarray(bearing, dtype=np.float64).reshape(-1) % 360.0
    ranges = np.asarray(distance, dtype=np.float64).reshape(-1)
    degenerate = ranges == 0.0
    start_azimuth = (desired + 180.0) % 360.0
    observer_lon = source_lon.copy()
    observer_lat = source_lat.copy()

    active = ~degenerate
    failed = np.zeros(source_lon.shape, dtype=np.bool_)
    residual = np.zeros(source_lon.shape, dtype=np.float64)
    converged = np.zeros(source_lon.shape, dtype=np.bool_)
    # Leave room for forward-azimuth rounding before the authoritative inverse check.
    iteration_tolerance_deg = tolerance_deg * 0.5
    back_azimuth = np.zeros(source_lon.shape, dtype=np.float64)
    if np.any(active):
        candidate_lon, candidate_lat, candidate_back_azimuth, valid = _forward_finite(
            source_lon[active], source_lat[active], start_azimuth[active], ranges[active]
        )
        active_indices = np.flatnonzero(active)
        observer_lon[active_indices[valid]] = candidate_lon[valid]
        observer_lat[active_indices[valid]] = candidate_lat[valid]
        back_azimuth[active_indices[valid]] = candidate_back_azimuth[valid]
        failed[active_indices[~valid]] = True
        residual[failed] = 180.0

    for iteration in range(max_iterations):
        pending = active & ~failed
        if not np.any(pending):
            break
        residual[pending] = _wrap_signed(back_azimuth[pending] - desired[pending])
        matched = np.abs(residual[pending]) <= iteration_tolerance_deg
        converged[pending] = matched
        pending_indices = np.flatnonzero(pending)
        pending = pending_indices[~matched]
        if iteration == max_iterations - 1 or pending.size == 0:
            break
        start_azimuth[pending] = (start_azimuth[pending] - residual[pending]) % 360.0
        candidate_lon, candidate_lat, candidate_back_azimuth, valid = _forward_finite(
            source_lon[pending],
            source_lat[pending],
            start_azimuth[pending],
            ranges[pending],
        )
        observer_lon[pending[valid]] = candidate_lon[valid]
        observer_lat[pending[valid]] = candidate_lat[valid]
        back_azimuth[pending[valid]] = candidate_back_azimuth[valid]
        failed[pending[~valid]] = True
        converged[pending[~valid]] = False
        residual[pending[~valid]] = 180.0

    # The inverse calculation is independent of the forward residual used above.
    check = active & ~failed
    if np.any(check):
        inverse_bearing, valid = _inverse_finite(
            observer_lon[check], observer_lat[check], source_lon[check], source_lat[check]
        )
        check_indices = np.flatnonzero(check)
        failed[check_indices[~valid]] = True
        residual[check_indices[~valid]] = 180.0
        valid_indices = check_indices[valid]
        residual[valid_indices] = _wrap_signed(inverse_bearing[valid] - desired[valid_indices])
        converged[valid_indices] = np.abs(residual[valid_indices]) <= tolerance_deg
    converged[degenerate | failed] = False

    positions = np.stack((observer_lon, observer_lat), axis=-1).reshape((*output_shape, 2))
    residual = residual.reshape(output_shape)
    converged = converged.reshape(output_shape)
    degenerate = degenerate.reshape(output_shape)
    for array in (positions, residual, converged, degenerate):
        array.setflags(write=False)
    return ReverseBearingResult(
        observer_lonlat_deg=positions,
        angular_residual_deg=residual,
        converged=converged,
        degenerate=degenerate,
    )


def _forward_finite(
    source_lon: NDArray[np.float64],
    source_lat: NDArray[np.float64],
    start_azimuth: NDArray[np.float64],
    ranges: NDArray[np.float64],
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64], NDArray[np.bool_]]:
    try:
        lon, lat, back_azimuth = _GEOD.fwd(
            source_lon,
            source_lat,
            start_azimuth,
            ranges,
            return_back_azimuth=True,
        )
        lon = np.asarray(lon, dtype=np.float64)
        lat = np.asarray(lat, dtype=np.float64)
        back_azimuth = np.asarray(back_azimuth, dtype=np.float64)
        valid = np.isfinite(lon) & np.isfinite(lat) & np.isfinite(back_azimuth)
    except (GeodError, OverflowError, ValueError):
        fallback = np.zeros(source_lon.shape, dtype=np.float64)
        return (
            source_lon.copy(),
            source_lat.copy(),
            fallback,
            np.zeros(source_lon.shape, dtype=np.bool_),
        )
    return (
        np.where(valid, lon, source_lon),
        np.where(valid, lat, source_lat),
        np.where(valid, back_azimuth, 0.0),
        valid,
    )


def _inverse_finite(
    observer_lon: NDArray[np.float64],
    observer_lat: NDArray[np.float64],
    catalog_lon: NDArray[np.float64],
    catalog_lat: NDArray[np.float64],
) -> tuple[NDArray[np.float64], NDArray[np.bool_]]:
    try:
        bearing, _, _ = _GEOD.inv(observer_lon, observer_lat, catalog_lon, catalog_lat)
        bearing = np.asarray(bearing, dtype=np.float64)
        valid = np.isfinite(bearing)
    except (GeodError, OverflowError, ValueError):
        return np.zeros(observer_lon.shape, dtype=np.float64), np.zeros(
            observer_lon.shape, dtype=np.bool_
        )
    return np.where(valid, bearing, 0.0), valid


def _as_finite_float_array(value: ArrayLike, name: str) -> NDArray[np.float64]:
    try:
        raw = np.asarray(value)
        if not np.issubdtype(raw.dtype, np.number) or np.issubdtype(raw.dtype, np.bool_):
            raise ValueError
        result = np.asarray(raw, dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must contain finite numeric values.") from exc
    if not np.all(np.isfinite(result)):
        raise ValueError(f"{name} must contain finite numeric values.")
    return result


def _wrap_signed(angle_deg: NDArray[np.float64]) -> NDArray[np.float64]:
    return (angle_deg + 180.0) % 360.0 - 180.0
