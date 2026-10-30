"""Approved small synthetic checks for the experimental resident path.

CPU calculations in this file are independent correctness references only.
They are outside the implementation search path.
"""

from dataclasses import replace

import numpy as np
import pytest
from pyproj import Geod, Transformer
from shapely.geometry import Polygon, box

from maritime.geometry.vector_map import VectorMap
from maritime.schemas.inputs_v2 import CatalogRecordV2, CatalogV2
from maritime.search.gpu.resident.engine import ResidentConfig, ResidentDevice, run_resident_search
from maritime.search.gpu.resident.preparation import prepare_world

TO_XY = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True)
ORIGIN = TO_XY.transform(-79.905, 32.77)


def _catalog(records=()):
    return CatalogV2(tuple(records), "2023-07-15T12:00:30Z", "test", "test", "test")


def _world(records=(), *, land=None, unknown=None):
    x, y = ORIGIN
    vm = VectorMap(
        land,
        crs="EPSG:32618",
        coverage_geometry=box(x - 30000, y - 30000, x + 30000, y + 30000),
        unknown_geometry=unknown,
    )
    world = prepare_world(
        vm,
        _catalog(records),
        chart_geometry=vm._coverage,
        catalog_complete=True,
        catalog_objects_cover_envelope=True,
        identities_certified=True,
        exact_positions_asserted=True,
        nominal_offsets_asserted=True,
        max_projection_scale=1.02,
        azimuth_error_deg=10.0,
    )
    return vm, world


def _gpu(world):
    cp = pytest.importorskip("cupy")
    if cp.cuda.runtime.getDeviceCount() == 0:
        pytest.skip("CUDA required; there is no CPU fallback")
    x, y = ORIGIN
    config = ResidentConfig(
        (x - 2, x + 2, y - 2, y + 2),
        (0,),
        terminal_size_m=1,
        numerical_validation_accepted=True,
        trusted_marker_labels=True,
        hard_bearing_bounds_asserted=True,
        object_capacity=32,
        batch_cells=8,
        roots_per_task=1,
    )
    return cp, ResidentDevice(world, config, 0)


def test_preparation_does_not_invent_certificates():
    x, y = ORIGIN
    vm = VectorMap(
        None, crs="EPSG:32618", coverage_geometry=box(x - 100, y - 100, x + 100, y + 100)
    )
    row = CatalogRecordV2("aid", 32.77, -79.905, normalized_type="physical_aid")
    world = prepare_world(vm, _catalog([row]))
    assert not world.chart_scope_certified
    assert not world.identities_complete
    assert not world.position_support_complete
    assert np.isinf(world.objects[0, 4])


def test_gpu_map_queries_match_independent_polygon_reference():
    x, y = ORIGIN
    land = Polygon(
        [(x + 10, y - 50), (x + 70, y - 50), (x + 70, y + 50), (x + 10, y + 50)],
        [[(x + 20, y - 10), (x + 40, y - 10), (x + 40, y + 10), (x + 20, y + 10)]],
    )
    vm, world = _world(land=land, unknown=box(x - 100, y + 80, x + 100, y + 100))
    cp, gpu = _gpu(world)
    points = np.asarray([(x, y), (x + 15, y), (x + 30, y), (x, y + 90), (x + 50000, y)])
    rays = np.asarray(
        [
            (x, y, x + 80, y),
            (x - 10, y - 100, x - 10, y + 50),
            (x, y + 70, x, y + 120),
            (x + 22, y, x + 38, y),
        ]
    )
    with cp.cuda.Device(0), gpu.stream:
        codes = cp.empty(len(points), dtype=cp.int32)
        gpu._launch(
            "query_points",
            len(points),
            (cp.asarray(points), np.int32(len(points)), *gpu.map_args, codes),
        )
        ray_codes = cp.empty(len(rays), dtype=cp.int32)
        gpu._launch(
            "query_rays",
            len(rays),
            (cp.asarray(rays), np.int32(len(rays)), *gpu.map_args, ray_codes),
        )
        gpu.stream.synchronize()
        assert cp.asnumpy(codes).tolist() == [
            {"water": 0, "land": 1, "unknown": 2}[vm.point_water_status(tuple(p))] for p in points
        ]
        assert cp.asnumpy(ray_codes).tolist() == [
            {"clear": 0, "blocked": 1, "unknown": 2}[vm.line_visibility(tuple(r[:2]), tuple(r[2:]))]
            for r in rays
        ]


def test_gpu_projection_and_ellipsoidal_bearings_match_proj():
    _, world = _world()
    cp, gpu = _gpu(world)
    ll = np.asarray([(-81.0, 25.0), (-79.905, 32.77), (-75.0, 37.0), (-70.0, 43.0), (-66.0, 45.0)])
    xx, yy = TO_XY.transform(ll[:, 0], ll[:, 1])
    xy = np.column_stack((xx, yy))
    target = ll + np.asarray((0.01, 0.01))
    expected_b, _, expected_d = Geod(ellps="WGS84").inv(
        ll[:, 0], ll[:, 1], target[:, 0], target[:, 1]
    )
    with cp.cuda.Device(0), gpu.stream:
        output = cp.empty((len(ll), 4), dtype=cp.float64)
        flags = cp.empty(len(ll), dtype=cp.int32)
        gpu._launch(
            "query_geodesics",
            len(ll),
            (cp.asarray(xy), cp.asarray(target), np.int32(len(ll)), output, flags),
        )
        gpu.stream.synchronize()
        got = cp.asnumpy(output)
        assert cp.asnumpy(flags).tolist() == [0] * len(ll)
    np.testing.assert_allclose(got[:, :2], ll, atol=1e-9, rtol=0)
    np.testing.assert_allclose(got[:, 2], expected_b % 360, atol=1e-7, rtol=0)
    np.testing.assert_allclose(got[:, 3], expected_d, atol=1e-4, rtol=0)


def test_marker_score_ignores_unused_markers_and_has_no_cpu_fallback(monkeypatch):
    geod = Geod(ellps="WGS84")
    lon, lat, _ = geod.fwd(-79.905, 32.77, 90.0, 1000.0)
    _, world = _world([CatalogRecordV2("aid", lat, lon, normalized_type="physical_aid")])
    cp, gpu = _gpu(world)

    def forbidden(*args, **kwargs):
        raise AssertionError("CPU query was called during resident search")

    monkeypatch.setattr(VectorMap, "point_water_status", forbidden)
    monkeypatch.setattr(VectorMap, "line_visibility", forbidden)
    import scipy.optimize

    import maritime.search.prediction as prediction
    import maritime.search.region_evidence as evidence

    monkeypatch.setattr(prediction, "predict_visible_groups", forbidden)
    monkeypatch.setattr(evidence, "build_region_evidence", forbidden)
    monkeypatch.setattr(scipy.optimize, "linear_sum_assignment", forbidden)
    x, y = ORIGIN
    with cp.cuda.Device(0), gpu.stream:
        result = gpu.score(
            cp.asarray([[x - 1, y - 1, x + 1, y + 1]]), cp.asarray([90.0]), cp.asarray([0.1])
        )
        gpu.stream.synchronize()
        assert cp.asnumpy(result["status"]).tolist() == [0]
        assert float(result["residual_deg"][0].item()) < 1e-6


def test_full_domain_keeps_all_surviving_leaf_cells(tmp_path):
    _, world = _world()
    _, gpu = _gpu(world)
    config = replace(gpu.config, initial_size_m=4.0, terminal_size_m=1.0)
    output = tmp_path / "run"
    result = run_resident_search(world, config, np.asarray([]), np.asarray([]), output)
    assert result["complete"]
    assert result["terminal"] == 16
    arrays = [np.load(path)["cells"] for path in output.rglob("leaf-*.npz")]
    assert sum(len(a) for a in arrays) == 16


def test_unverified_numerics_and_missing_certificates_disable_marker_pruning():
    _, world = _world()
    _, gpu = _gpu(world)
    unverified = ResidentDevice(world, replace(gpu.config, numerical_validation_accepted=False), 0)
    assert not unverified.marker_proofs


@pytest.mark.parametrize(
    "name",
    ["trusted_marker_labels", "hard_bearing_bounds_asserted", "numerical_validation_accepted"],
)
@pytest.mark.parametrize("bad", ["false", 0])
def test_config_rejects_truthy_nonboolean_certificates(name, bad):
    with pytest.raises(TypeError, match="must be a bool"):
        ResidentConfig((0, 1, 0, 1), (0,), **{name: bad})


@pytest.mark.parametrize(
    "name",
    [
        "catalog_complete",
        "catalog_objects_cover_envelope",
        "identities_certified",
        "exact_positions_asserted",
        "nominal_offsets_asserted",
    ],
)
def test_preparation_rejects_truthy_nonboolean_certificates(name):
    x, y = ORIGIN
    vm = VectorMap(
        None, crs="EPSG:32618", coverage_geometry=box(x - 100, y - 100, x + 100, y + 100)
    )
    with pytest.raises(TypeError, match="must be a bool"):
        prepare_world(vm, _catalog(), **{name: "false"})


@pytest.mark.parametrize("offset_m", [-0.0005, 0.0, 0.0005])
def test_range_cutoff_numerical_band_is_unresolved(offset_m):
    lon, lat, _ = Geod(ellps="WGS84").fwd(-79.905, 32.77, 90.0, 10000.0 + offset_m)
    _, world = _world([CatalogRecordV2("edge", lat, lon, normalized_type="physical_aid")])
    cp, gpu = _gpu(world)
    x, y = ORIGIN
    with cp.cuda.Device(0), gpu.stream:
        result = gpu.score(
            cp.asarray([[x - 1, y - 1, x + 1, y + 1]]), cp.asarray([90.0]), cp.asarray([0.1])
        )
        gpu.stream.synchronize()
        assert int(result["status"][0].item()) == 2
        assert int(result["flags"][0].item()) & 8


@pytest.mark.parametrize("offset_deg", [-0.0000025, -0.0000005, 0.0, 0.0000005, 0.0000025])
def test_bearing_cutoff_numerical_band_is_unresolved(offset_deg):
    lon, lat, _ = Geod(ellps="WGS84").fwd(-79.905, 32.77, 90.0, 1000.0)
    _, world = _world([CatalogRecordV2("edge", lat, lon, normalized_type="physical_aid")])
    cp, gpu = _gpu(world)
    x, y = ORIGIN
    with cp.cuda.Device(0), gpu.stream:
        result = gpu.score(
            cp.asarray([[x - 1, y - 1, x + 1, y + 1]]),
            cp.asarray([90.1 + offset_deg]),
            cp.asarray([0.1]),
        )
        gpu.stream.synchronize()
        assert int(result["status"][0].item()) == 2
        assert int(result["flags"][0].item()) & 8


def test_instrumented_tiny_traversal_has_cuda_launches_without_cpu_query_fallback(
    monkeypatch, tmp_path
):
    import json
    from collections import Counter

    lon, lat, _ = Geod(ellps="WGS84").fwd(-79.905, 32.77, 90.0, 1000.0)
    _, world = _world([CatalogRecordV2("aid", lat, lon, normalized_type="physical_aid")])
    cp, gpu = _gpu(world)
    counts = Counter()
    original_launch = ResidentDevice._launch
    original_init = ResidentDevice.__init__

    def launch(self, name, n, args):
        if n:
            counts[name] += 1
        return original_launch(self, name, n, args)

    def initialize(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        original_assignment = self.assignment

        def assignment(*a, **kw):
            counts["batched_hungarian"] += 1
            return original_assignment(*a, **kw)

        self.assignment = assignment

    def forbidden(*args, **kwargs):
        counts["cpu_query_fallback"] += 1
        raise AssertionError("CPU query invoked during strict GPU traversal")

    monkeypatch.setattr(ResidentDevice, "_launch", launch)
    monkeypatch.setattr(ResidentDevice, "__init__", initialize)
    monkeypatch.setattr(VectorMap, "point_water_status", forbidden)
    monkeypatch.setattr(VectorMap, "line_visibility", forbidden)
    import scipy.optimize

    import maritime.search.prediction as prediction
    import maritime.search.region_evidence as evidence

    monkeypatch.setattr(evidence, "build_region_evidence", forbidden)
    monkeypatch.setattr(prediction, "predict_visible_groups", forbidden)
    monkeypatch.setattr(scipy.optimize, "linear_sum_assignment", forbidden)
    config = replace(gpu.config, initial_size_m=4.0, terminal_size_m=1.0)
    result = run_resident_search(
        world, config, np.asarray([90.0]), np.asarray([0.2]), tmp_path / "run"
    )
    for name in (
        "seed_cells",
        "prune_cells",
        "predict_groups",
        "candidate_scope",
        "batched_hungarian",
    ):
        assert counts[name] > 0
    assert counts["cpu_query_fallback"] == 0
    assert result["complete"] and result["terminal"] == 16
    device_name = cp.cuda.runtime.getDeviceProperties(0)["name"]
    if isinstance(device_name, bytes):
        device_name = device_name.decode()
    report = dict(
        method="instrumented CUDA dispatch plus CPU query guards; not a CUDA timeline",
        device=device_name,
        kernel_dispatch_counts=dict(counts),
        cpu_query_fallback_calls=counts["cpu_query_fallback"],
        cpu_coordination_and_io_present=True,
        completion=result,
    )
    (tmp_path / "gpu-launch-audit.json").write_text(json.dumps(report, indent=2) + "\n")


def test_near_range_ties_of_different_types_are_unresolved():
    geod = Geod(ellps="WGS84")
    lon1, lat1, _ = geod.fwd(-79.905, 32.77, 90.0, 1000.0)
    lon2, lat2, _ = geod.fwd(-79.905, 32.77, 90.01, 1000.0005)
    _, world = _world(
        [
            CatalogRecordV2("aid", lat1, lon1, normalized_type="physical_aid"),
            CatalogRecordV2("boat", lat2, lon2, normalized_type="vessel"),
        ]
    )
    cp, gpu = _gpu(world)
    x, y = ORIGIN
    with cp.cuda.Device(0), gpu.stream:
        result = gpu.score(
            cp.asarray([[x - 1, y - 1, x + 1, y + 1]]), cp.asarray([90.0]), cp.asarray([0.1])
        )
        gpu.stream.synchronize()
        assert int(result["status"][0].item()) == 2
        assert int(result["flags"][0].item()) & 8


def test_nearly_coincident_marker_bearing_is_unresolved():
    lon, lat, _ = Geod(ellps="WGS84").fwd(-79.905, 32.77, 90.0, 0.0005)
    _, world = _world([CatalogRecordV2("near", lat, lon, normalized_type="physical_aid")])
    cp, gpu = _gpu(world)
    x, y = ORIGIN
    with cp.cuda.Device(0), gpu.stream:
        result = gpu.score(
            cp.asarray([[x - 1, y - 1, x + 1, y + 1]]), cp.asarray([90.0]), cp.asarray([0.1])
        )
        gpu.stream.synchronize()
        assert int(result["status"][0].item()) == 2
        assert int(result["flags"][0].item()) & 8
