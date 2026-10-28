"""Whole-cell true-bearing witnesses and independent chart-completeness proofs."""

import numpy as np
import pytest
from pyproj import Geod, Transformer
from shapely.geometry import box

from maritime.geometry.vector_map import VectorMap
from maritime.schemas.inputs_v2 import CatalogRecordV2, CatalogV2
from maritime.search.gpu.resident.chart_pruning import module_for, prune_chart_geometry
from maritime.search.gpu.resident.engine import ResidentConfig, ResidentDevice

PROJECT = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True)
INVERSE = Transformer.from_crs("EPSG:32618", "EPSG:4326", always_xy=True)
GEOD = Geod(ellps="WGS84")
ORIGIN = PROJECT.transform(-79.905, 32.77)


def _resident(records=(), *, complete=True, bounded=True):
    from maritime.search.gpu.resident.preparation import prepare_world

    cp = pytest.importorskip("cupy")
    if cp.cuda.runtime.getDeviceCount() <= 3:
        pytest.skip("GPU3 is required to leave the running search untouched")
    x, y = ORIGIN
    catalog = CatalogV2(tuple(records), "2023-07-15T12:00:30Z", "test", "test", "test")
    world = prepare_world(
        VectorMap(None, crs="EPSG:32618"),
        catalog,
        chart_geometry=box(x - 100000, y - 100000, x + 100000, y + 100000),
        catalog_complete=complete,
        catalog_objects_cover_envelope=complete,
        identities_certified=True,
        exact_positions_asserted=bounded,
        nominal_offsets_asserted=True,
        max_projection_scale=2.0,
        azimuth_error_deg=30.0,
    )
    config = ResidentConfig(
        (x - 1, x + 1, y - 1, y + 1),
        (3,),
        radius_m=1000.0,
        trusted_marker_labels=True,
        hard_bearing_bounds_asserted=True,
        numerical_validation_accepted=True,
    )
    return cp, ResidentDevice(world, config, 3)


def _record(distance=500.0, bearing=0.0):
    lon, lat, _ = GEOD.fwd(-79.905, 32.77, bearing, distance)
    return CatalogRecordV2("aid", lat, lon, normalized_type="physical_aid")


@pytest.mark.parametrize("case", ["empty", "far", "bearing", "count", "valid"])
def test_independent_complete_chart_proofs_work_without_shoreline(case):
    records = () if case == "empty" else (_record(5000.0 if case == "far" else 500.0),)
    cp, gpu = _resident(records)
    bearings = [180.0] if case == "bearing" else [0.0, 0.1] if case == "count" else [0.0]
    x, y = ORIGIN
    with cp.cuda.Device(3), gpu.stream:
        cells = cp.asarray([[x - 1, y - 1, x + 1, y + 1]])
        obs, tol = cp.asarray(bearings), cp.full(len(bearings), 0.5)
        baseline = gpu.prune(cells, obs, tol)
        independent = prune_chart_geometry(gpu, cells, obs, tol, true_bearings=False)
        improved = prune_chart_geometry(gpu, cells, obs, tol, true_bearings=True)
        gpu.stream.synchronize()
        assert int(baseline[0].item()) == 0
        expected = 0 if case == "valid" else 3 if case == "count" else 2
        assert int(independent[0].item()) == expected
        assert int(improved[0].item()) == expected


@pytest.mark.parametrize("complete,bounded", [(False, True), (True, False)])
def test_missing_catalog_or_position_certificate_never_creates_a_prune(complete, bounded):
    cp, gpu = _resident((_record(5000.0),), complete=complete, bounded=bounded)
    x, y = ORIGIN
    with cp.cuda.Device(3), gpu.stream:
        state = prune_chart_geometry(
            gpu, cp.asarray([[x - 1, y - 1, x + 1, y + 1]]), cp.asarray([180.0]), cp.asarray([0.5])
        )
        gpu.stream.synchronize()
        assert int(state[0].item()) == 0


def _bounds(cp, gpu, cells, objects):
    output = cp.empty((len(cells), 4), dtype=cp.float64)
    module_for(gpu, True).get_function("query_true_bearing_bounds")(
        ((len(cells) + 127) // 128,),
        (128,),
        (
            cp.asarray(cells),
            cp.asarray(objects),
            np.int32(len(cells)),
            np.float64(0.001),
            np.float64(0.000001),
            output,
        ),
    )
    gpu.stream.synchronize()
    return cp.asnumpy(output)


def test_true_bearing_envelope_contains_interior_boundary_and_object_uncertainty_witnesses():
    cp, gpu = _resident()
    rng = np.random.default_rng(20261028)
    cells = []
    objects = []
    witnesses = []
    for _ in range(128):
        lon = float(rng.uniform(-81.5, -66.0))
        lat = float(rng.uniform(25.0, 45.0))
        x, y = PROJECT.transform(lon, lat)
        side = float(10 ** rng.uniform(0.0, 4.0))
        cell = np.array([x - side / 2, y - side / 2, x + side / 2, y + side / 2])
        target_lon, target_lat, _ = GEOD.fwd(
            lon, lat, float(rng.uniform(0, 360)), float(rng.uniform(100.0, 12000.0))
        )
        ox, oy = PROJECT.transform(target_lon, target_lat)
        U = float(rng.uniform(0.0, 100.0))
        cells.append(cell)
        objects.append([ox, oy, target_lon, target_lat, U, 0.0, 1.0, 1.0])
        positions = [
            (cell[0], cell[1]),
            (cell[0], cell[3]),
            (cell[2], cell[1]),
            (cell[2], cell[3]),
            (x, y),
        ]
        positions += list(zip(rng.uniform(cell[0], cell[2], 4), rng.uniform(cell[1], cell[3], 4)))
        observed = []
        for px, py in positions:
            plon, plat = INVERSE.transform(px, py)
            for direction in range(0, 360, 45):
                tlon, tlat, _ = GEOD.fwd(target_lon, target_lat, float(direction), U)
                angle, _, distance = GEOD.inv(plon, plat, tlon, tlat)
                observed.append((angle % 360, distance))
        witnesses.append(observed)
    with cp.cuda.Device(3), gpu.stream:
        result = _bounds(cp, gpu, np.asarray(cells), np.asarray(objects))
    supported = 0
    for output, observed in zip(result, witnesses, strict=True):
        angle, expansion, lower, valid = output
        if valid:
            supported += 1
        for actual, distance in observed:
            error = abs((actual - angle + 180) % 360 - 180)
            assert error <= expansion + 1e-6
            assert lower <= distance + 0.001
    assert supported > 100


def test_true_bearing_allowance_shrinks_with_cells_and_unsupported_domain_retains():
    cp, gpu = _resident()
    x, y = ORIGIN
    lon, lat, _ = GEOD.fwd(-79.905, 32.77, 90.0, 10000.0)
    ox, oy = PROJECT.transform(lon, lat)
    cells = np.asarray(
        [[x - w / 2, y - w / 2, x + w / 2, y + w / 2] for w in [1000.0, 100.0, 10.0, 1.0]]
    )
    objects = np.tile([ox, oy, lon, lat, 0.0, 0.0, 1.0, 1.0], (4, 1))
    with cp.cuda.Device(3), gpu.stream:
        result = _bounds(cp, gpu, cells, objects)
        invalid = _bounds(cp, gpu, np.asarray([[2000000.0, 0.0, 2000001.0, 1.0]]), objects[:1])
    assert np.all(np.diff(result[:, 1]) < 0)
    assert result[-1, 1] < 0.01
    assert invalid[0, 1] == 180.0 and invalid[0, 2] == 0.0 and invalid[0, 3] == 0.0


def test_true_bearing_mode_does_not_require_the_old_projection_angle_allowance():
    from dataclasses import replace

    from maritime.search.gpu.resident.optimized import OptimizedResidentDevice

    cp, old = _resident()
    world = replace(old.world, azimuth_error_deg=None)
    new = OptimizedResidentDevice(world, old.config, 3)
    fallback = OptimizedResidentDevice(world, old.config, 3, independent_chart_pruning=False)
    assert new.marker_proofs
    assert not fallback.marker_proofs
    x, y = ORIGIN
    with cp.cuda.Device(3), new.stream:
        result = new.prune(
            cp.asarray([[x - 1, y - 1, x + 1, y + 1]]), cp.asarray([0.0]), cp.asarray([0.5])
        )
        new.stream.synchronize()
        assert int(result[0].item()) == 2


def test_analytic_inverse_projection_derivative_bounds_admit_point_99_scale():
    f = 1 / 298.257223563
    n = f / (2 - f)
    n2, n3, n4, n5, n6 = n**2, n**3, n**4, n**5, n**6
    beta = np.asarray(
        [
            n / 2 - 2 * n2 / 3 + 37 * n3 / 96 - n4 / 360 - 81 * n5 / 512 + 96199 * n6 / 604800,
            n2 / 48 + n3 / 15 - 437 * n4 / 1440 + 46 * n5 / 105 - 1118711 * n6 / 3870720,
            17 * n3 / 480 - 37 * n4 / 840 - 209 * n5 / 4480 + 5569 * n6 / 90720,
            4397 * n4 / 161280 - 11 * n5 / 504 - 830251 * n6 / 7257600,
            4583 * n5 / 161280 - 108847 * n6 / 3991680,
            20648693 * n6 / 638668800,
        ]
    )
    j = np.arange(1, 7)
    derivative_upper = 1 + np.sum(2 * j * np.abs(beta) * np.cosh(2 * j * 0.16))
    assert derivative_upper < 1.002
    ground_lipschitz_upper = 1.002 * 1.00168 / (0.9966 * 0.9996)
    assert ground_lipschitz_upper < 1 / 0.99
