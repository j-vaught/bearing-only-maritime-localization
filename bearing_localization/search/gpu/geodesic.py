"""Optional CuPy FP64 kernels for short-range WGS84 reverse-bearing proposals.

Equations follow Vincenty's nested direct/inverse ellipsoid solution (Survey
Review 23(176), 88-93, 1975). Output is proposal geometry; exact CPU geodesic
checks remain authoritative before downstream scoring.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from importlib import import_module
from math import isfinite
from time import perf_counter
from typing import Any, Literal

import numpy as np
from numpy.typing import ArrayLike, NDArray
from pyproj import Geod

from maritime_localization_shared.geospatial.reverse_bearing import solve_reverse_bearing_observer

_A = 6_378_137.0
_F = 1.0 / 298.257223563
_B = _A * (1.0 - _F)
_MAX_RANGE_M = 20_000.0


@dataclass(frozen=True, slots=True)
class GeodesicGPUProposalResult:
    """Broadcast-shaped reverse-geodesic positions with explicit status."""

    stable_ids: Sequence[str | int]
    broadcast_shape: tuple[int, ...]
    observer_lonlat_deg: NDArray[np.float64]
    inverse_bearing_deg: NDArray[np.float64]
    angular_residual_deg: NDArray[np.float64]
    range_error_m: NDArray[np.float64]
    converged: NDArray[np.bool_]
    degenerate: NDArray[np.bool_]
    backend: Literal["cupy", "cpu_fallback"]
    chunks_processed: int
    host_to_device_bytes: int
    device_to_host_bytes: int
    host_to_device_seconds: float
    synchronized_work_seconds: float
    device_to_host_seconds: float
    elapsed_seconds: float


def solve_reverse_bearing_cupy(
    catalog_lonlat_deg: ArrayLike,
    desired_bearing_deg: ArrayLike,
    range_m: ArrayLike,
    *,
    stable_ids: Sequence[str | int] | None = None,
    max_pairs_per_chunk: int = 16_384,
    tolerance_deg: float = 1e-7,
    fallback_to_cpu: bool = False,
    cupy_module: Any | None = None,
    collect_timings: bool = False,
) -> GeodesicGPUProposalResult:
    """Return observer proposals for catalog points, bearings, and ranges.

    Inputs broadcast together. Bearings are clockwise from north, from the
    unknown observer toward each catalog point. Supported catalog latitudes
    are 23..50 degrees and distances are 0..20,000 m. Zero range returns the
    catalog point with degenerate=True and converged=False. Out-of-domain
    inputs raise NotImplementedError unless explicit CPU fallback is enabled.
    """
    elapsed_start = perf_counter()
    if type(collect_timings) is not bool:
        raise ValueError("collect_timings must be a bool.")
    catalog = _finite_array(catalog_lonlat_deg, "catalog_lonlat_deg")
    if catalog.ndim < 1 or catalog.shape[-1] != 2 or catalog.size == 0:
        raise ValueError("catalog_lonlat_deg must have a nonempty final axis of length two.")
    lon, lat = catalog[..., 0], catalog[..., 1]
    if np.any((lon < -180.0) | (lon > 180.0)):
        raise ValueError("catalog longitude must be within [-180, 180] degrees.")
    if np.any((lat < -90.0) | (lat > 90.0)):
        raise ValueError("catalog latitude must be within [-90, 90] degrees.")
    bearing = _finite_array(desired_bearing_deg, "desired_bearing_deg")
    distance = _finite_array(range_m, "range_m")
    if np.any(distance < 0.0):
        raise ValueError("range_m must be nonnegative.")
    if type(max_pairs_per_chunk) is not int or max_pairs_per_chunk < 1:
        raise ValueError("max_pairs_per_chunk must be a positive integer.")
    if not isfinite(tolerance_deg) or not 0.0 < tolerance_deg <= 1e-7:
        raise ValueError("tolerance_deg must be in (0, 1e-7].")
    unsupported = np.any((lat < 23.0) | (lat > 50.0)) or np.any(distance > _MAX_RANGE_M)
    if unsupported:
        if fallback_to_cpu:
            return _cpu_fallback(catalog, bearing, distance, stable_ids, tolerance_deg)
        raise NotImplementedError(
            "CuPy reverse geodesics support catalog latitude 23..50 degrees and ranges 0..20,000 m."
        )
    try:
        lon_b, lat_b, bearing_b, distance_b = np.broadcast_arrays(lon, lat, bearing, distance)
    except ValueError as exc:
        raise ValueError("catalog, bearing, and range inputs must broadcast together.") from exc
    shape = lon_b.shape
    flat_lon = np.asarray(lon_b, dtype=np.float64).reshape(-1)
    flat_lat = np.asarray(lat_b, dtype=np.float64).reshape(-1)
    flat_bearing = np.asarray(bearing_b, dtype=np.float64).reshape(-1) % 360.0
    flat_range = np.asarray(distance_b, dtype=np.float64).reshape(-1)
    ids = _stable_ids(stable_ids, flat_lon.size)
    cp = cupy_module if cupy_module is not None else import_module("cupy")

    result_xy = np.column_stack((flat_lon, flat_lat))
    result_bearing = np.zeros(flat_lon.size)
    result_residual = np.zeros(flat_lon.size)
    result_range_error = np.zeros(flat_lon.size)
    result_converged = np.zeros(flat_lon.size, dtype=np.bool_)
    degenerate = flat_range == 0.0
    chunks = h2d = d2h = 0
    h2d_seconds = work_seconds = d2h_seconds = 0.0

    for start in range(0, flat_lon.size, max_pairs_per_chunk):
        stop = min(start + max_pairs_per_chunk, flat_lon.size)
        indexes = np.arange(start, stop)
        indexes = indexes[~degenerate[start:stop]]
        if not indexes.size:
            continue
        host = (
            flat_lon[indexes],
            flat_lat[indexes],
            flat_bearing[indexes],
            flat_range[indexes],
        )
        h2d += sum(array.nbytes for array in host)
        with cp.cuda.Device():
            if collect_timings:
                phase_start = perf_counter()
            source_lon, source_lat, desired, distance_gpu = (
                cp.asarray(array, dtype=cp.float64) for array in host
            )
            if collect_timings:
                cp.cuda.get_current_stream().synchronize()
                h2d_seconds += perf_counter() - phase_start
                phase_start = perf_counter()
            start_azimuth = (desired + 180.0) % 360.0
            for _ in range(12):
                out_lon, out_lat, reverse_azimuth = _direct(
                    cp, source_lon, source_lat, start_azimuth, distance_gpu
                )
                residual = _wrap(reverse_azimuth - desired)
                start_azimuth = cp.where(
                    cp.abs(residual) > tolerance_deg * 0.5,
                    (start_azimuth - residual) % 360.0,
                    start_azimuth,
                )
            out_lon, out_lat, _ = _direct(cp, source_lon, source_lat, start_azimuth, distance_gpu)
            bearing_check, distance_check, valid = _inverse(
                cp, out_lon, out_lat, source_lon, source_lat
            )
            angular_error = _wrap(bearing_check - desired)
            range_error = distance_check - distance_gpu
            converged = (
                valid
                & cp.isfinite(out_lon)
                & cp.isfinite(out_lat)
                & cp.isfinite(angular_error)
                & cp.isfinite(range_error)
                & (cp.abs(angular_error) <= tolerance_deg)
                & (cp.abs(range_error) <= 1e-3)
            )
            if collect_timings:
                cp.cuda.get_current_stream().synchronize()
                work_seconds += perf_counter() - phase_start
                phase_start = perf_counter()
            host_result = cp.asnumpy(
                cp.stack(
                    (
                        out_lon,
                        out_lat,
                        bearing_check,
                        angular_error,
                        range_error,
                        converged.astype(cp.float64),
                    ),
                    axis=1,
                )
            )
            if collect_timings:
                cp.cuda.get_current_stream().synchronize()
                d2h_seconds += perf_counter() - phase_start
        d2h += host_result.nbytes
        result_xy[indexes] = host_result[:, :2]
        result_bearing[indexes] = host_result[:, 2]
        result_residual[indexes] = host_result[:, 3]
        result_range_error[indexes] = host_result[:, 4]
        result_converged[indexes] = host_result[:, 5] != 0.0
        chunks += 1

    arrays = (
        result_xy.reshape((*shape, 2)),
        result_bearing.reshape(shape),
        result_residual.reshape(shape),
        result_range_error.reshape(shape),
        result_converged.reshape(shape),
        degenerate.reshape(shape),
    )
    for array in arrays:
        array.setflags(write=False)
    return GeodesicGPUProposalResult(
        ids,
        shape,
        *arrays,
        "cupy",
        chunks,
        int(h2d),
        int(d2h),
        h2d_seconds if collect_timings else 0.0,
        work_seconds if collect_timings else 0.0,
        d2h_seconds if collect_timings else 0.0,
        perf_counter() - elapsed_start if collect_timings else 0.0,
    )


def _direct(cp: Any, lon1: Any, lat1: Any, azimuth: Any, distance: Any):
    """Vectorized Vincenty direct solve; return reverse endpoint azimuth."""
    phi1, alpha1 = cp.deg2rad(lat1), cp.deg2rad(azimuth)
    tan_u1 = (1.0 - _F) * cp.tan(phi1)
    cos_u1 = 1.0 / cp.sqrt(1.0 + tan_u1 * tan_u1)
    sin_u1 = tan_u1 * cos_u1
    sin_alpha1, cos_alpha1 = cp.sin(alpha1), cp.cos(alpha1)
    sigma1 = cp.arctan2(tan_u1, cos_alpha1)
    sin_alpha = cos_u1 * sin_alpha1
    cos2_alpha = 1.0 - sin_alpha * sin_alpha
    u2 = cos2_alpha * (6_378_137.0**2 - _B**2) / _B**2
    aa = 1 + u2 / 16384 * (4096 + u2 * (-768 + u2 * (320 - 175 * u2)))
    bb = u2 / 1024 * (256 + u2 * (-128 + u2 * (74 - 47 * u2)))
    sigma = distance / (_B * aa)
    for _ in range(6):
        two_sigma_m = 2 * sigma1 + sigma
        ss, cs, c2 = cp.sin(sigma), cp.cos(sigma), cp.cos(two_sigma_m)
        delta = (
            bb
            * ss
            * (
                c2
                + bb
                / 4
                * (cs * (-1 + 2 * c2 * c2) - bb / 6 * c2 * (-3 + 4 * ss * ss) * (-3 + 4 * c2 * c2))
            )
        )
        sigma = distance / (_B * aa) + delta
    ss, cs = cp.sin(sigma), cp.cos(sigma)
    two_sigma_m = 2 * sigma1 + sigma
    tmp = sin_u1 * ss - cos_u1 * cs * cos_alpha1
    phi2 = cp.arctan2(
        sin_u1 * cs + cos_u1 * ss * cos_alpha1,
        (1 - _F) * cp.sqrt(sin_alpha * sin_alpha + tmp * tmp),
    )
    lam = cp.arctan2(ss * sin_alpha1, cos_u1 * cs - sin_u1 * ss * cos_alpha1)
    cc = _F / 16 * cos2_alpha * (4 + _F * (4 - 3 * cos2_alpha))
    c2 = cp.cos(two_sigma_m)
    longitude_delta = lam - (1 - cc) * _F * sin_alpha * (
        sigma + cc * ss * (c2 + cc * cs * (-1 + 2 * c2 * c2))
    )
    lon2 = (lon1 + cp.rad2deg(longitude_delta) + 180) % 360 - 180
    lat2 = cp.rad2deg(phi2)
    alpha2_forward = cp.arctan2(sin_alpha, -tmp)
    back_azimuth = (cp.rad2deg(alpha2_forward) + 180) % 360
    return lon2, lat2, back_azimuth


def _inverse(cp: Any, lon1: Any, lat1: Any, lon2: Any, lat2: Any):
    """Vectorized Vincenty inverse solve; return initial azimuth and range."""
    phi1, phi2 = cp.deg2rad(lat1), cp.deg2rad(lat2)
    delta_lon = cp.deg2rad((lon2 - lon1 + 180) % 360 - 180)
    u1, u2 = cp.arctan((1 - _F) * cp.tan(phi1)), cp.arctan((1 - _F) * cp.tan(phi2))
    su1, cu1, su2, cu2 = cp.sin(u1), cp.cos(u1), cp.sin(u2), cp.cos(u2)
    lam = delta_lon.copy()
    for _ in range(8):
        sl, cl = cp.sin(lam), cp.cos(lam)
        ss = cp.sqrt((cu2 * sl) ** 2 + (cu1 * su2 - su1 * cu2 * cl) ** 2)
        cs = su1 * su2 + cu1 * cu2 * cl
        sigma = cp.arctan2(ss, cs)
        salpha = cu1 * cu2 * sl / ss
        c2alpha = 1 - salpha * salpha
        c2sm = cp.where(c2alpha > 1e-30, cs - 2 * su1 * su2 / cp.maximum(c2alpha, 1e-30), 0.0)
        c = _F / 16 * c2alpha * (4 + _F * (4 - 3 * c2alpha))
        lam = delta_lon + (1 - c) * _F * salpha * (
            sigma + c * ss * (c2sm + c * cs * (-1 + 2 * c2sm * c2sm))
        )
    sl, cl = cp.sin(lam), cp.cos(lam)
    ss = cp.sqrt((cu2 * sl) ** 2 + (cu1 * su2 - su1 * cu2 * cl) ** 2)
    cs = su1 * su2 + cu1 * cu2 * cl
    sigma = cp.arctan2(ss, cs)
    salpha = cu1 * cu2 * sl / ss
    c2alpha = 1 - salpha * salpha
    c2sm = cp.where(c2alpha > 1e-30, cs - 2 * su1 * su2 / cp.maximum(c2alpha, 1e-30), 0.0)
    u2term = c2alpha * (6_378_137.0**2 - _B**2) / _B**2
    aa = 1 + u2term / 16384 * (4096 + u2term * (-768 + u2term * (320 - 175 * u2term)))
    bb = u2term / 1024 * (256 + u2term * (-128 + u2term * (74 - 47 * u2term)))
    delta = (
        bb
        * ss
        * (
            c2sm
            + bb
            / 4
            * (
                cs * (-1 + 2 * c2sm * c2sm)
                - bb / 6 * c2sm * (-3 + 4 * ss * ss) * (-3 + 4 * c2sm * c2sm)
            )
        )
    )
    distance = _B * aa * (sigma - delta)
    bearing = cp.rad2deg(cp.arctan2(cu2 * sl, cu1 * su2 - su1 * cu2 * cl)) % 360
    valid = cp.isfinite(bearing) & cp.isfinite(distance) & (ss > 0) & (distance >= 0)
    return bearing, distance, valid


def _cpu_fallback(catalog, bearing, distance, stable_ids, tolerance_deg):
    try:
        lon, lat, desired, ranges = np.broadcast_arrays(
            catalog[..., 0], catalog[..., 1], bearing, distance
        )
    except ValueError as exc:
        raise ValueError("catalog, bearing, and range inputs must broadcast together.") from exc
    shape = lon.shape
    ids = _stable_ids(stable_ids, lon.size)
    solved = solve_reverse_bearing_observer(
        catalog, bearing, distance, tolerance_deg=min(1e-9, tolerance_deg)
    )
    positions = np.array(solved.observer_lonlat_deg, copy=True)
    geod = Geod(ellps="WGS84")
    azimuth, _, actual_range = geod.inv(positions[..., 0], positions[..., 1], lon, lat)
    residual = (azimuth - desired % 360 + 180) % 360 - 180
    errors = np.asarray(actual_range) - ranges
    degenerate = np.array(solved.degenerate, copy=True)
    residual = np.where(degenerate, 0.0, residual)
    errors = np.where(degenerate, 0.0, errors)
    converged = (
        np.asarray(solved.converged)
        & np.isfinite(residual)
        & np.isfinite(errors)
        & (np.abs(residual) <= tolerance_deg)
        & (np.abs(errors) <= 1e-3)
    )
    arrays = (positions, np.asarray(azimuth), residual, errors, converged, degenerate)
    for array in arrays:
        array.setflags(write=False)
    return GeodesicGPUProposalResult(
        ids, shape, *arrays, "cpu_fallback", 0, 0, 0, 0.0, 0.0, 0.0, 0.0
    )


def _stable_ids(stable_ids: Sequence[str | int] | None, size: int):
    ids: Sequence[str | int] = range(size) if stable_ids is None else tuple(stable_ids)
    if len(ids) != size:
        raise ValueError("stable_ids length must equal flattened broadcast size.")
    if any(type(item) not in (str, int) for item in ids) or len(set(ids)) != len(ids):
        raise ValueError("stable_ids must contain unique strings or integers.")
    return ids


def _finite_array(value: ArrayLike, name: str) -> NDArray[np.float64]:
    try:
        raw = np.asarray(value)
        if not np.issubdtype(raw.dtype, np.number) or np.issubdtype(raw.dtype, np.bool_):
            raise ValueError
        out = np.asarray(raw, dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must contain finite numeric values.") from exc
    if not np.all(np.isfinite(out)):
        raise ValueError(f"{name} must contain finite numeric values.")
    return out


def _wrap(value: Any):
    return (value + 180.0) % 360.0 - 180.0


__all__ = ["GeodesicGPUProposalResult", "solve_reverse_bearing_cupy"]
