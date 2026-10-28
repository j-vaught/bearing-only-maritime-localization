"""Tiny device parity checks for the opt-in candidate/object-pair path."""


import numpy as np
import pytest
from pyproj import Geod, Transformer
from shapely.geometry import box

from maritime.geometry.vector_map import VectorMap
from maritime.schemas.inputs_v2 import CatalogRecordV2, CatalogV2
from maritime.search.gpu.resident.engine import ResidentConfig, ResidentDevice
from maritime.search.gpu.resident.parallel_prediction import predict_groups_parallel
from maritime.search.gpu.resident.preparation import prepare_world

ORIGIN = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True).transform(-79.905, 32.77)


def _gpu(records, *, capacity=32, land=None, unknown=None):
    cp = pytest.importorskip("cupy")
    if cp.cuda.runtime.getDeviceCount() < 4:
        pytest.skip("This check is restricted to physical CUDA device 3")
    x, y = ORIGIN
    vm = VectorMap(
        land,
        crs="EPSG:32618",
        coverage_geometry=box(x - 30000, y - 30000, x + 30000, y + 30000),
        unknown_geometry=unknown,
    )
    catalog = CatalogV2(tuple(records), "2023-07-15T12:00:30Z", "test", "1", "test")
    world = prepare_world(
        vm,
        catalog,
        chart_geometry=vm._coverage,
        catalog_complete=True,
        catalog_objects_cover_envelope=True,
        identities_certified=True,
        exact_positions_asserted=True,
        nominal_offsets_asserted=True,
        max_projection_scale=1.02,
        azimuth_error_deg=10.0,
    )
    cfg = ResidentConfig(
        (x - 2, x + 2, y - 2, y + 2),
        (3,),
        object_capacity=capacity,
        numerical_validation_accepted=True,
        trusted_marker_labels=True,
        hard_bearing_bounds_asserted=True,
    )
    return cp, ResidentDevice(world, cfg, 3)


def _record(key, bearing, distance, kind="physical_aid", width=0.0):
    lon, lat, _ = Geod(ellps="WGS84").fwd(-79.905, 32.77, bearing, distance)
    return CatalogRecordV2(
        key, lat, lon, normalized_type=kind, metadata=(("angular_half_width_deg", width),)
    )


def _baseline(resident, cells):
    cp, cfg, w = resident.cp, resident.config, resident.world
    b, g = len(cells), cfg.object_capacity
    angles, ranges, ga, ge = (cp.empty((b, g), dtype=cp.float64) for _ in range(4))
    ids, exact, gi, gx = (cp.empty((b, g), dtype=cp.int32) for _ in range(4))
    gc, flags, point = (cp.empty(b, dtype=cp.int32) for _ in range(3))
    resident._launch(
        "predict_groups",
        b,
        (
            cells,
            np.int32(b),
            *resident.map_args,
            *resident.object_args,
            np.float64(cfg.radius_m),
            np.float64(cfg.range_numerical_margin_m),
            np.float64(cfg.bearing_numerical_margin_deg),
            np.float64(w.max_projection_scale or 1),
            np.float64(cfg.angular_resolution_deg),
            np.int32(g),
            angles,
            ranges,
            ids,
            exact,
            ga,
            ge,
            gi,
            gx,
            gc,
            flags,
            point,
        ),
    )
    return dict(
        group_angles=ga,
        group_extents=ge,
        group_ids=gi,
        group_exact=gx,
        group_counts=gc,
        flags=flags,
        point_states=point,
    )


def _compare(resident, xy):
    cp = resident.cp
    with cp.cuda.Device(3), resident.stream:
        xy = cp.asarray(xy, dtype=cp.float64)
        cells = cp.concatenate((xy - 0.25, xy + 0.25), axis=1)
        reference = _baseline(resident, cells)
        got = predict_groups_parallel(resident, cells)
        resident.stream.synchronize()
        for key in ("group_counts", "flags", "point_states"):
            np.testing.assert_array_equal(
                cp.asnumpy(got[key]), cp.asnumpy(reference[key]), err_msg=key
            )
        valid = (
            cp.arange(resident.config.object_capacity)[None, :] < reference["group_counts"][:, None]
        )
        for key in ("group_angles", "group_extents"):
            np.testing.assert_allclose(
                cp.asnumpy(got[key][valid]),
                cp.asnumpy(reference[key][valid]),
                atol=1e-9,
                rtol=0,
                err_msg=key,
            )
        for key in ("group_ids", "group_exact"):
            np.testing.assert_array_equal(
                cp.asnumpy(got[key][valid]), cp.asnumpy(reference[key][valid]), err_msg=key
            )
        return {key: cp.asnumpy(value) for key, value in got.items()}


def test_parallel_prediction_matches_circular_grouping_vessel_rep_and_extents():
    records = [
        _record("a", 359.98, 1000.0, width=0.02),
        _record("b", 0.02, 900.0),
        _record("c", 90.0, 1000.0),
        _record("d", 90.03, 800.0, "vessel"),
        _record("e", 180.0, 2000.0),
        _record("f", 250.0, 1500.0, "unknown"),
    ]
    _, gpu = _gpu(records)
    x, y = ORIGIN
    got = _compare(gpu, [[x, y], [x + 2, y - 2], [x - 2, y + 2]])
    assert got["group_counts"][0] == 3


def test_parallel_prediction_preserves_blocked_unknown_and_observer_states():
    x, y = ORIGIN
    records = [
        _record("east", 90.0, 1000.0),
        _record("north", 0.0, 1500.0),
        _record("west", 270.0, 1500.0),
    ]
    _, gpu = _gpu(
        records,
        land=box(x + 100, y - 100, x + 200, y + 100),
        unknown=box(x - 100, y + 600, x + 100, y + 700),
    )
    _compare(gpu, [[x, y], [x + 150, y], [x, y + 650], [x + 40000, y]])


def test_parallel_prediction_preserves_capacity_overflow_prefix():
    x, y = ORIGIN
    records = [_record(str(j), j * 50.0, 500.0 + j * 100.0) for j in range(6)]
    _, gpu = _gpu(records, capacity=2, unknown=box(x - 50, y + 300, x + 50, y + 400))
    got = _compare(gpu, [[x, y], [x + 1, y + 1]])
    assert np.all(got["flags"] & 2)
    assert np.all(got["group_counts"] == 0)


def test_parallel_prediction_preserves_numerical_boundary_flags():
    x, y = ORIGIN
    records = [
        _record("near", 0.0, 0.0005),
        _record("edge", 90.0, 9999.9995),
        _record("tie-a", 180.0, 1000.0),
        _record("tie-b", 180.02, 1000.0005),
        _record("angular-a", 250.0, 1500.0),
        _record("angular-b", 250.1, 2000.0),
    ]
    _, gpu = _gpu(records)
    got = _compare(gpu, [[x, y], [x + 1, y + 1]])
    assert got["flags"][0] & 8


def test_parallel_prediction_empty_and_random_small_catalog_parity():
    x, y = ORIGIN
    _, empty = _gpu([])
    _compare(empty, [[x, y], [x + 40000, y]])
    rng = np.random.default_rng(114)
    records = [
        _record(
            f"r{j:02}",
            float(rng.uniform(0, 360)),
            float(rng.uniform(100, 12000)),
            ("physical_aid", "vessel", "unknown")[j % 3],
            width=0.01 * (j % 2),
        )
        for j in range(14)
    ]
    _, gpu = _gpu(records)
    _compare(gpu, [[x, y], [x + 20, y - 10], [x - 10, y + 20]])
