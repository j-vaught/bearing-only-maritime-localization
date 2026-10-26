"""Accuracy and contract checks for optional GPU reverse-geodesic proposals."""

import numpy as np
import pytest
from pyproj import Geod

from bearing_localization.search.gpu.geodesic import solve_reverse_bearing_cupy
from maritime_localization_shared.geospatial.reverse_bearing import solve_reverse_bearing_observer


def _cupy():
    cp = pytest.importorskip("cupy", reason="optional GPU extra is not installed")
    if cp.cuda.runtime.getDeviceCount() < 1:
        pytest.skip("no CUDA device available")
    return cp


def _check_oracle(result, catalog, bearings, ranges):
    geod = Geod(ellps="WGS84")
    catalog = np.asarray(catalog, dtype=np.float64)
    lon, lat, desired, distance = np.broadcast_arrays(
        catalog[..., 0], catalog[..., 1], bearings, ranges
    )
    oracle = solve_reverse_bearing_observer(catalog, bearings, ranges)
    oracle_pos = oracle.observer_lonlat_deg
    lon1, lat1 = result.observer_lonlat_deg[..., 0], result.observer_lonlat_deg[..., 1]
    lon2, lat2 = oracle_pos[..., 0], oracle_pos[..., 1]
    _, _, position_error = geod.inv(lon1, lat1, lon2, lat2)
    azimuth, _, recovered_range = geod.inv(lon1, lat1, lon, lat)
    residual = (azimuth - desired % 360 + 180) % 360 - 180
    active = distance > 0
    assert np.all(np.isfinite(result.observer_lonlat_deg))
    assert np.all(np.isfinite(result.inverse_bearing_deg))
    assert np.all(np.isfinite(result.angular_residual_deg))
    assert np.all(np.isfinite(result.range_error_m))
    assert np.all(result.converged[active])
    assert np.max(np.abs(position_error[active]), initial=0.0) <= 1e-3
    assert np.max(np.abs(residual[active]), initial=0.0) <= 1e-7
    assert np.max(np.abs(recovered_range[active] - distance[active]), initial=0.0) <= 1e-3
    assert np.max(np.abs(result.angular_residual_deg[active]), initial=0.0) <= 1e-7
    assert np.max(np.abs(result.range_error_m[active]), initial=0.0) <= 1e-3
    assert np.all(result.degenerate[~active])
    assert not np.any(result.converged[~active])


def test_optional_gpu_import_and_unsupported_scope_contract():
    with pytest.raises(NotImplementedError, match="latitude 23..50"):
        solve_reverse_bearing_cupy([-70.0, 20.0], 10.0, 100.0, cupy_module=object())
    fallback = solve_reverse_bearing_cupy(
        [-70.0, 20.0], 10.0, 100.0, fallback_to_cpu=True, stable_ids=("south",)
    )
    assert fallback.backend == "cpu_fallback"
    assert fallback.stable_ids == ("south",)
    assert bool(fallback.converged)


def test_validation_and_stable_id_contract():
    with pytest.raises(ValueError, match="nonnegative"):
        solve_reverse_bearing_cupy([-70.0, 35.0], 0.0, -1.0)
    with pytest.raises(ValueError, match="unique"):
        solve_reverse_bearing_cupy(
            [[-70.0, 35.0], [-71.0, 36.0]], [0.0, 90.0], [1.0, 2.0], stable_ids=(4, 4)
        )
    with pytest.raises(ValueError, match="broadcast"):
        solve_reverse_bearing_cupy(
            np.tile([-70.0, 35.0], (2, 1)), np.zeros(3), np.ones(2), cupy_module=object()
        )


def test_gpu_broadcast_corner_wrap_range_and_chunk_contract():
    cp = _cupy()
    catalog = np.array([[-80.2, 25.8], [-77.0, 34.0], [-73.5, 40.7], [-67.1, 49.9]])
    bearings = np.array([0.0, 45.0, 90.0, 135.0, 180.0, 225.0, 270.0, 315.0])
    ranges = np.array([0.0, 125.0, 1000.0, 5000.0, 10000.0, 20000.0])
    # (catalog, bearing, range) gives complete coverage of all three axes.
    catalog_grid = np.broadcast_to(catalog[:, None, None, :], (4, 8, 6, 2))
    bearing_grid = np.broadcast_to(bearings[None, :, None], (4, 8, 6))
    range_grid = np.broadcast_to(ranges[None, None, :], (4, 8, 6))
    ids = tuple(f"c{c}-b{b}-r{r}" for c in range(4) for b in range(8) for r in range(6))
    outputs = [
        solve_reverse_bearing_cupy(
            catalog_grid,
            bearing_grid,
            range_grid,
            stable_ids=ids,
            max_pairs_per_chunk=n,
            cupy_module=cp,
        )
        for n in (1, 17, 16384)
    ]
    for result, result_chunk_size in zip(outputs, (1, 17, 16384), strict=True):
        _check_oracle(result, catalog_grid, bearing_grid, range_grid)
        assert result.broadcast_shape == (4, 8, 6)
        assert result.stable_ids == ids
        active = np.broadcast_to(range_grid != 0.0, (4, 8, 6)).reshape(-1)
        expected_chunks = sum(
            np.any(active[start : start + result_chunk_size])
            for start in range(0, active.size, result_chunk_size)
        )
        assert result.chunks_processed == expected_chunks
        assert result.host_to_device_bytes == 160 * 32
        assert result.device_to_host_bytes == 160 * 48
    np.testing.assert_array_equal(outputs[0].observer_lonlat_deg, outputs[-1].observer_lonlat_deg)
    assert outputs[0].host_to_device_bytes == outputs[-1].host_to_device_bytes


def test_gpu_seeded_random_full_domain_and_antimeridian():
    cp = _cupy()
    rng = np.random.default_rng(28428)
    n = 1024
    catalog = np.column_stack((rng.uniform(-180.0, 180.0, n), rng.uniform(23.0, 50.0, n)))
    catalog[:4, 0] = [179.999999, -179.999999, 180.0, -180.0]
    catalog[:4, 1] = [23.0, 50.0, 23.0, 50.0]
    bearing = rng.uniform(-720.0, 720.0, n)
    ranges = rng.uniform(0.1, 20_000.0, n)
    ids = tuple(f"rand-{i}" for i in range(n))
    for chunk in (31, 512, 2048):
        result = solve_reverse_bearing_cupy(
            catalog, bearing, ranges, stable_ids=ids, max_pairs_per_chunk=chunk, cupy_module=cp
        )
        _check_oracle(result, catalog, bearing, ranges)
        assert result.chunks_processed == int(np.ceil(n / chunk))
        assert result.host_to_device_bytes == n * 32
        assert result.device_to_host_bytes == n * 48


def test_gpu_internal_half_tolerance_resolves_captured_a04_roundoff_case():
    cp = _cupy()
    catalog = (-80.98065189999943, 31.796930200000133)
    desired_bearing = 215.65789052601954
    range_m = 625.0
    result = solve_reverse_bearing_cupy(catalog, desired_bearing, range_m, cupy_module=cp)
    geod = Geod(ellps="WGS84")
    inverse_bearing, _, distance = geod.inv(
        float(result.observer_lonlat_deg[0]),
        float(result.observer_lonlat_deg[1]),
        catalog[0],
        catalog[1],
    )
    angular_error = (inverse_bearing - desired_bearing + 180.0) % 360.0 - 180.0
    assert bool(result.converged)
    assert abs(float(result.angular_residual_deg)) <= 1e-7
    assert abs(angular_error) <= 1e-7
    assert distance == pytest.approx(range_m, abs=1e-3)


def test_default_stable_ids_are_lazy_numeric_indices():
    result = solve_reverse_bearing_cupy(
        [[-70.0, 35.0], [-71.0, 36.0]], [15.0, 20.0], [0.0, 0.0], cupy_module=object()
    )
    assert isinstance(result.stable_ids, range)
    assert result.stable_ids[0] == 0
    assert result.stable_ids[-1] == 1
